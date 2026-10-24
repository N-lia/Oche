package ai.oyi.device.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import ai.oyi.device.MainActivity
import ai.oyi.device.OyiApplication
import ai.oyi.device.R
import ai.oyi.device.data.UsageTracker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Foreground service that keeps OYI alive on the phone.
 *
 * It owns the overlay bubble, the usage tracker, and the outbound WebSocket
 * link to the OYI gateway.
 */
class OyiService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    private lateinit var container: ai.oyi.device.AppContainer
    private lateinit var overlay: OverlayController
    private lateinit var usageTracker: UsageTracker
    private var screenReceiver: BroadcastReceiver? = null
    private var settingsJob: Job? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as OyiApplication).container

        createNotificationChannel()
        startForegroundCompat()

        overlay = OverlayController(this, container.runtime, container.usage, scope)
        usageTracker = UsageTracker(this, container.usage, container.runtime, scope)
        usageTracker.start()

        container.deviceClient.start()

        registerScreenReceiver()

        settingsJob = scope.launch {
            container.settings.settings.collect { settings ->
                if (settings.overlayEnabled) {
                    overlay.show()
                } else {
                    overlay.hide()
                }
            }
        }

        scope.launch { usageTracker.sync() }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                container.settings.setOverlayEnabled(false)
                stopSelf()
                return START_NOT_STICKY
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        screenReceiver?.let { runCatching { unregisterReceiver(it) } }
        screenReceiver = null
        settingsJob?.cancel()
        usageTracker.stop()
        container.deviceClient.stop()
        overlay.hide()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun registerScreenReceiver() {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_OFF -> container.runtime.clearForeground()
                    Intent.ACTION_SCREEN_ON, Intent.ACTION_USER_PRESENT -> {
                        scope.launch { usageTracker.sync() }
                    }
                }
            }
        }
        screenReceiver = receiver
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_USER_PRESENT)
        }
        ContextCompat.registerReceiver(
            this,
            receiver,
            filter,
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.notification_channel_description)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun startForegroundCompat() {
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE,
            )
        } else {
            // Older platforms do not know the "specialUse" type.
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, OyiService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_oyi)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText("Overlay, usage tracking and the OYI link are running.")
            .setContentIntent(openIntent)
            .addAction(0, getString(R.string.notification_stop), stopIntent)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        const val ACTION_STOP = "ai.oyi.device.action.STOP"
        private const val CHANNEL_ID = "oyi_device"
        private const val NOTIFICATION_ID = 4201

        /** True when OYI may draw the bubble over other apps. */
        fun hasOverlayPermission(context: Context): Boolean =
            Settings.canDrawOverlays(context)
    }
}
