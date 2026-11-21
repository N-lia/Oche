package ai.oyi.device.service

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import ai.oyi.device.MainActivity
import ai.oyi.device.R
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import ai.oyi.device.runtime.DeviceRuntime
import ai.oyi.device.runtime.isAgentBusy
import ai.oyi.device.ui.island.IslandIdleInfo
import ai.oyi.device.ui.island.islandSpec
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch

/**
 * Mirrors what the island shows as an Android 16 Live Update.
 *
 * On Android 16 an ongoing notification that asks to be promoted shows as a
 * chip in the status bar and at the top of the shade. That makes it a free
 * fallback for when the island cannot draw: the overlay is off or not
 * allowed. It is posted only while something is under way (a turn, phone
 * control, an approval) and withdrawn as soon as it ends. On older Android
 * the same notification is simply a quiet ongoing one.
 */
class LiveUpdateNotifier(
    private val context: Context,
    private val runtime: DeviceRuntime,
    private val settings: SettingsStore,
    private val overlayShowing: () -> Boolean,
    private val scope: CoroutineScope,
) {
    private var job: Job? = null
    private var posted = false

    fun start() {
        if (job != null) return
        createChannel()
        job = scope.launch {
            combine(runtime.island, runtime.appVisible, settings.settings) { island, appVisible, prefs ->
                Triple(island, appVisible, prefs.liveUpdates)
            }
                .distinctUntilChanged()
                .collect { (island, appVisible, enabled) ->
                    val wanted = enabled && isLive(island) && !appVisible && !overlayShowing()
                    if (wanted) post(island) else cancel()
                }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
        cancel()
    }

    private fun isLive(state: IslandState): Boolean =
        state.kind.isAgentBusy() ||
            state.kind == IslandKind.WORKING ||
            state.kind == IslandKind.APPROVAL_REQUIRED

    private fun post(state: IslandState) {
        if (!canNotify()) return
        val spec = islandSpec(state, IslandIdleInfo(), expanded = false)
        val builder = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_oyi)
            .setContentTitle(spec.title)
            .setContentText(spec.detail.ifBlank { spec.badge.lowercase().replaceFirstChar { it.uppercase() } })
            .setContentIntent(open())
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(
                if (state.kind == IslandKind.WORKING) NotificationCompat.CATEGORY_PROGRESS else NotificationCompat.CATEGORY_STATUS,
            )
            // Ask Android 16 to promote it to a Live Update chip, with a
            // short label for the chip itself.
            .addExtras(
                Bundle().apply {
                    putBoolean(EXTRA_REQUEST_PROMOTED_ONGOING, true)
                    putCharSequence(EXTRA_SHORT_CRITICAL_TEXT, chipText(state))
                },
            )
        if (state.startedAt > 0) {
            builder.setWhen(state.startedAt).setShowWhen(true).setUsesChronometer(true)
        }
        if (state.kind != IslandKind.APPROVAL_REQUIRED) builder.setProgress(0, 0, true)
        when (state.kind) {
            IslandKind.APPROVAL_REQUIRED -> {
                builder.addAction(0, "Deny", command(OyiService.ACTION_DENY, 11))
                builder.addAction(0, "Approve", command(OyiService.ACTION_APPROVE, 12))
            }
            IslandKind.WORKING -> builder.addAction(0, "Stop", command(OyiService.ACTION_CANCEL, 13))
            IslandKind.SPEAKING -> builder.addAction(0, "Stop talking", command(OyiService.ACTION_INTERRUPT, 14))
            else -> Unit
        }
        runCatching { manager()?.notify(NOTIFICATION_ID, builder.build()) }
        posted = true
    }

    private fun cancel() {
        if (!posted) return
        posted = false
        runCatching { manager()?.cancel(NOTIFICATION_ID) }
    }

    private fun chipText(state: IslandState): String = when (state.kind) {
        IslandKind.THINKING -> "Thinking"
        IslandKind.TOOL -> "Tool"
        IslandKind.REPLYING -> "Replying"
        IslandKind.SPEAKING -> "Talking"
        IslandKind.WORKING -> if (state.step > 0) "Step ${state.step}" else "Phone"
        IslandKind.APPROVAL_REQUIRED -> "Needs you"
        else -> "OYI"
    }

    private fun canNotify(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    private fun manager(): NotificationManager? = context.getSystemService(NotificationManager::class.java)

    private fun open(): PendingIntent = PendingIntent.getActivity(
        context,
        10,
        Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun command(action: String, requestCode: Int): PendingIntent = PendingIntent.getService(
        context,
        requestCode,
        Intent(context, OyiService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = manager() ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        // Promoted notifications need a channel above "low"; it stays silent.
        val channel = NotificationChannel(CHANNEL_ID, "Live activity", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "What OYI is doing right now, while the island is hidden."
            setSound(null, null)
            enableVibration(false)
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private companion object {
        const val CHANNEL_ID = "oyi_live"
        const val NOTIFICATION_ID = 4202

        // Notification.EXTRA_REQUEST_PROMOTED_ONGOING and
        // EXTRA_SHORT_CRITICAL_TEXT (API 36), spelled out so this builds
        // against SDK 35. Older Android ignores them.
        const val EXTRA_REQUEST_PROMOTED_ONGOING = "android.requestPromotedOngoing"
        const val EXTRA_SHORT_CRITICAL_TEXT = "android.shortCriticalText"
    }
}
