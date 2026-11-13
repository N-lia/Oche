package ai.oyi.device

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.core.content.ContextCompat
import ai.oyi.device.service.OyiService
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import ai.oyi.device.ui.MainScreen

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        applyDebugOverrides(intent)
        ensureOyiServiceRunning()
        setContent {
            OyiTheme {
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

@Composable
private fun OyiTheme(content: @Composable () -> Unit) {
    val colors = darkColorScheme(
        primary = Color(0xFFF08A65),
        onPrimary = Color(0xFF0A0807),
        primaryContainer = Color(0xFFD9724E),
        onPrimaryContainer = Color(0xFF0A0807),
        secondary = Color(0xFFF7EDD0),
        onSecondary = Color(0xFF0B0A08),
        secondaryContainer = Color(0xFFE9DCBA),
        onSecondaryContainer = Color(0xFF0B0A08),
        tertiary = Color(0xFFF7E27E),
        onTertiary = Color(0xFF0B0A08),
        background = Color(0xFF070708),
        onBackground = Color(0xFFF8F6F1),
        surface = Color(0xFF151518),
        onSurface = Color(0xFFF8F6F1),
        surfaceVariant = Color(0xFF1F1F24),
        onSurfaceVariant = Color(0xFFA09C94),
        error = Color(0xFFF08A65),
        onError = Color(0xFF0A0807),
        errorContainer = Color(0xFF2E1B14),
        onErrorContainer = Color(0xFFF7EDD0),
        outline = Color(0xFF38383E),
    )
    MaterialTheme(colorScheme = colors, content = content)
}
