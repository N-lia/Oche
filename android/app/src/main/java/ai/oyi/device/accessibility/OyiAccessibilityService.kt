package ai.oyi.device.accessibility

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import ai.oyi.device.OyiApplication

/**
 * Accessibility service that powers phone control.
 *
 * It does two things:
 *  1. Reports the foreground app so the overlay can show it.
 *  2. Exposes the screen (accessibility tree + screenshot) and executes one UI
 *     action at a time on behalf of the OYI vision planner.
 */
class OyiAccessibilityService : AccessibilityService() {

    private val runtime
        get() = (application as OyiApplication).container.runtime

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        runtime.controller = DeviceController(this)
        runtime.onAccessibilityReady(true)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        if (event.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return
        val packageName = event.packageName?.toString() ?: return
        runtime.onForeground(packageName, event.eventTime.takeIf { it > 0 } ?: System.currentTimeMillis())
    }

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        instance = null
        runtime.onAccessibilityReady(false)
        super.onDestroy()
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        runtime.onAccessibilityReady(false)
        return super.onUnbind(intent)
    }

    companion object {
        @Volatile
        var instance: OyiAccessibilityService? = null
            private set
    }
}
