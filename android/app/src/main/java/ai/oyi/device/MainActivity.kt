package ai.oyi.device

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import android.graphics.Color as AndroidColor
import androidx.core.content.ContextCompat
import ai.oyi.device.service.OyiService
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import ai.oyi.device.ui.MainScreen
import ai.oyi.device.ui.theme.OyiTheme

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Draw under the status and navigation bars: the black runs to the
        // edges and the island sits right under the camera.
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
        )
        applyDebugOverrides(intent)
        ensureOyiServiceRunning()
        val settings = (application as OyiApplication).container.settings.settings
        setContent {
            val prefs by settings.collectAsStateWithLifecycle()
            OyiTheme(wallpaperColors = prefs.wallpaperColors) {
                MainScreen()
            }
        }
    }

    /**
     * The overlay service is a foreground service, but the system still kills
     * it when the app is updated or force-stopped. Bring it back so the bubble
     * does not silently disappear.
     */
    private fun ensureOyiServiceRunning() {
        val settings = (application as OyiApplication).container.settings.settings.value
        if (!settings.overlayEnabled) return
        if (!Settings.canDrawOverlays(this)) return
        runCatching {
            ContextCompat.startForegroundService(this, Intent(this, OyiService::class.java))
        }
    }

    // While OYI is in front, its own header shows the island and the
    // overlay steps aside.
    override fun onStart() {
        super.onStart()
        (application as OyiApplication).container.runtime.setAppVisible(true)
    }

    override fun onStop() {
        (application as OyiApplication).container.runtime.setAppVisible(false)
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        applyDebugOverrides(intent)
    }

    /**
     * Debug builds accept the gateway settings as intent extras so a device can
     * be paired from a terminal:
     *
     *   adb shell am start -n ai.oyi.device.debug/ai.oyi.device.MainActivity \
     *     --es oyi_gateway_url http://10.0.2.2:8099 --es oyi_token <token>
     *
     * Release builds ignore this completely.
     */
    private fun applyDebugOverrides(intent: Intent?) {
        if (!BuildConfig.DEBUG) return
        val url = intent?.getStringExtra(EXTRA_GATEWAY_URL) ?: return
        val token = intent?.getStringExtra(EXTRA_TOKEN) ?: return
        (application as OyiApplication).container.settings.saveGateway(url, token)
    }

    private companion object {
        const val EXTRA_GATEWAY_URL = "oyi_gateway_url"
        const val EXTRA_TOKEN = "oyi_token"
    }
}
