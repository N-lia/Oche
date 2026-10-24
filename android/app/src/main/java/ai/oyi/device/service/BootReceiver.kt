package ai.oyi.device.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat
import ai.oyi.device.OyiApplication

/** Restarts the OYI service after a reboot or an app update. */
class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action
        if (action != Intent.ACTION_BOOT_COMPLETED && action != Intent.ACTION_MY_PACKAGE_REPLACED) {
            return
        }

        val settings = (context.applicationContext as OyiApplication).container.settings.settings.value
        if (!settings.overlayEnabled) return

        try {
            ContextCompat.startForegroundService(
                context,
                Intent(context, OyiService::class.java),
            )
        } catch (error: Exception) {
            Log.w("OyiBoot", "Could not start OYI after boot", error)
        }
    }
}
