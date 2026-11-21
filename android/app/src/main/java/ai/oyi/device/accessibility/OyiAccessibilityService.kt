package ai.oyi.device.accessibility

import android.accessibilityservice.AccessibilityService
import android.os.SystemClock
import android.view.accessibility.AccessibilityEvent
import ai.oyi.device.OyiApplication
import ai.oyi.device.focus.BrowserUrls

/**
 * Accessibility service that powers phone control.
 *
 * It does two things:
 *  1. Reports the foreground app so the overlay can show it.
 *  2. Exposes the screen (accessibility tree + screenshot) and executes one UI
 *     action at a time on behalf of the OYI vision planner.
 *  3. Tells the focus guard which app and which site are in front, so blocked
 *     ones can be covered at once.
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

    private val focusGuard
        get() = (application as OyiApplication).container.focusGuard

    private var lastUrlCheck = 0L

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return
        val packageName = event.packageName?.toString() ?: return
        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                // event.eventTime is uptimeMillis (time since boot), not wall
                // clock, so it must not be used as the "since" timestamp.
                runtime.onForeground(packageName, System.currentTimeMillis())
                focusGuard.onWindowEvent(packageName)
                checkBrowserSite(packageName)
            }
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> checkBrowserSite(packageName)
        }
    }

    /**
     * Reads the address bar of a known browser and hands the site to the
     * focus guard. Content events are frequent, so everything that is not a
     * browser with a block list returns before touching the window.
     */
    private fun checkBrowserSite(packageName: String) {
        val ids = BrowserUrls.URL_BAR_IDS[packageName] ?: return
        val guard = focusGuard
        if (!guard.watchesSites) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastUrlCheck < URL_CHECK_INTERVAL_MS) return
        lastUrlCheck = now
        try {
            val root = rootInActiveWindow ?: return
            if (root.packageName?.toString() != packageName) return
            for (id in ids) {
                val bar = root.findAccessibilityNodeInfosByViewId(id)?.firstOrNull() ?: continue
                // While the user is typing, the bar holds a draft, not a page.
                if (bar.isFocused) return
                val host = BrowserUrls.hostOf(bar.text?.toString().orEmpty()) ?: return
                guard.onBrowserHost(packageName, host)
                return
            }
        } catch (_: Exception) {
            // The window can change under us; the next event retries.
        }
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
        private const val URL_CHECK_INTERVAL_MS = 500L

        @Volatile
        var instance: OyiAccessibilityService? = null
            private set
    }
}
