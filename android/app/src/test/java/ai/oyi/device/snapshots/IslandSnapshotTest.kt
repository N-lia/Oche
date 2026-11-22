package ai.oyi.device.snapshots

import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandIdleInfo
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import com.android.ide.common.rendering.api.SessionParams
import org.junit.Rule
import org.junit.Test

/**
 * Renders every state of the Dynamic Island on the JVM.
 *
 * The images land in app/build/reports/paparazzi; CI uploads them as the
 * "island-snapshots" artifact so the island can be reviewed without a phone.
 */
class IslandSnapshotTest {

    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = DeviceConfig.PIXEL_5,
        theme = "android:Theme.Material.NoActionBar",
        renderingMode = SessionParams.RenderingMode.SHRINK,
    )

    private val now = System.currentTimeMillis()
    private val idle = IslandIdleInfo(
        appLabel = "YouTube",
        sessionMs = 754_000,
        todayMs = 3_900_000,
        link = ConnectionState.CONNECTED,
    )

    @Test
    fun agentTurn() = paparazzi.snapshot {
        Gallery(
            "Idle" to Sample(IslandState()),
            "Idle, tapped" to Sample(IslandState(), expanded = true),
            "Listening" to Sample(IslandState(kind = IslandKind.LISTENING, title = "Hearing you…"), level = 0.7f),
            "Thinking" to Sample(IslandState(kind = IslandKind.THINKING, title = "Thinking…", startedAt = now - 2_300)),
            "Tool" to Sample(
                IslandState(
                    kind = IslandKind.TOOL,
                    title = "Searching the web",
                    startedAt = now - 3_100,
                    tools = listOf("Searching the web"),
                ),
            ),
            "Tool, tapped" to Sample(
                IslandState(
                    kind = IslandKind.TOOL,
                    title = "Reading memory",
                    toolDone = true,
                    tools = listOf("Searching the web", "Reading memory"),
                ),
                expanded = true,
            ),
            "Speaking" to Sample(IslandState(kind = IslandKind.SPEAKING, title = "Speaking", startedAt = now - 4_000)),
            "Done" to Sample(IslandState(kind = IslandKind.COMPLETED, title = "Done", elapsedMs = 4_200, fromAgent = true)),
        )
    }

    @Test
    fun phoneAndAlerts() = paparazzi.snapshot {
        Gallery(
            "Phone control" to Sample(
                IslandState(kind = IslandKind.WORKING, title = "Controlling Settings", subtitle = "Tapping Wi-Fi", step = 3),
            ),
            "Phone control, tapped" to Sample(
                IslandState(kind = IslandKind.WORKING, title = "Controlling Settings", subtitle = "Tapping Wi-Fi", step = 3),
                expanded = true,
            ),
            "Approval" to Sample(
                IslandState(
                    kind = IslandKind.APPROVAL_REQUIRED,
                    title = "Send this WhatsApp message?",
                    subtitle = "\"On my way, see you at 6\" to John",
                    commandId = "c1",
                ),
            ),
            "Phone task done" to Sample(
                IslandState(
                    kind = IslandKind.COMPLETED,
                    title = "Task completed",
                    subtitle = "Opened Settings and turned on dark theme.",
                    commandId = "c1",
                ),
            ),
            "Focus nudge" to Sample(
                IslandState(
                    kind = IslandKind.ERROR,
                    title = "Back to your essay?",
                    subtitle = "3 minutes on Instagram during your focus block.",
                ),
            ),
            "Offline" to Sample(IslandState(), idle = IslandIdleInfo(link = ConnectionState.DISCONNECTED)),
        )
    }

    @Test
    fun cameraAndStacking() = paparazzi.snapshot {
        val camera = DpSize(26.dp, 26.dp)
        Gallery(
            "Two at once: phone control, OYI talking behind" to Sample(
                IslandState(kind = IslandKind.WORKING, title = "Controlling Settings", subtitle = "Tapping Wi-Fi", step = 3),
                secondary = IslandState(kind = IslandKind.SPEAKING, title = "Speaking", fromAgent = true),
            ),
            "On the camera, resting" to Sample(IslandState(), cutout = camera),
            "On the camera, thinking" to Sample(
                IslandState(kind = IslandKind.THINKING, title = "Thinking…", startedAt = now - 2_300),
                cutout = camera,
            ),
            "On the camera, approval" to Sample(
                IslandState(
                    kind = IslandKind.APPROVAL_REQUIRED,
                    title = "Send this WhatsApp message?",
                    subtitle = "\"On my way, see you at 6\" to John",
                    commandId = "c1",
                ),
                cutout = camera,
            ),
            "Wallpaper accent" to Sample(
                IslandState(kind = IslandKind.TOOL, title = "Searching the web", startedAt = now - 3_100),
                accent = Color(0xFF8AB4F8),
            ),
        )
    }

    private data class Sample(
        val state: IslandState,
        val expanded: Boolean = false,
        val level: Float = 0f,
        val idle: IslandIdleInfo? = null,
        val secondary: IslandState? = null,
        val cutout: DpSize = DpSize.Zero,
        val accent: Color? = null,
    )

    @Composable
    private fun Gallery(vararg samples: Pair<String, Sample>) = Still {
        Column(
            modifier = Modifier
                .width(412.dp)
                .background(Color(0xFF1C1B20))
                .padding(vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            samples.forEach { (label, sample) ->
                Text(label, color = Color(0xFF9C978E), fontSize = 11.sp)
                DynamicIsland(
                    state = sample.state,
                    idle = sample.idle ?: idle,
                    level = sample.level,
                    onAction = {},
                    initiallyExpanded = sample.expanded,
                    secondary = sample.secondary,
                    cutout = sample.cutout,
                    accent = sample.accent ?: Color(0xFFF08A65),
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
            }
        }
    }
}