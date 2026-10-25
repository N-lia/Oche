package ai.oyi.device

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
        setContent {
            OyiTheme {
                MainScreen()
            }
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
    val dark = isSystemInDarkTheme()
    val colors = if (dark) {
        darkColorScheme(
            primary = Color(0xFF7C5CFF),
            background = Color(0xFF12101C),
            surface = Color(0xFF1B1830),
            surfaceVariant = Color(0xFF241F3D),
        )
    } else {
        lightColorScheme(
            primary = Color(0xFF5B3DF5),
            background = Color(0xFFF7F5FF),
            surface = Color(0xFFFFFFFF),
            surfaceVariant = Color(0xFFEDEAFF),
        )
    }
    MaterialTheme(colorScheme = colors, content = content)
}
