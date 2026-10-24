package ai.oyi.device

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
        setContent {
            OyiTheme {
                MainScreen()
            }
        }
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
