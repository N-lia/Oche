package ai.oyi.device.snapshots

import ai.oyi.device.focus.Distraction
import ai.oyi.device.focus.FocusLive
import ai.oyi.device.focus.FocusState
import ai.oyi.device.focus.LIMIT_BLOCKED
import ai.oyi.device.focus.LIMIT_FOCUS_ONLY
import ai.oyi.device.model.AppUsage
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.ForegroundApp
import ai.oyi.device.ui.FocusActions
import ai.oyi.device.ui.FocusContent
import ai.oyi.device.ui.MainShell
import ai.oyi.device.ui.SpaceActions
import ai.oyi.device.ui.SpaceContent
import ai.oyi.device.ui.UiState
import ai.oyi.device.ui.theme.OyiTheme
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import org.junit.Rule
import org.junit.Test

/** Whole scrolling screens on a very tall phone, so every card can be reviewed at once. */
class FullLengthSnapshotTest {

    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = DeviceConfig.PIXEL_5.copy(screenHeight = 5600),
        theme = "android:Theme.Material.NoActionBar",
    )

    private val now = 1_763_700_000_000L

    @Test
    fun spaceAll() = paparazzi.snapshot {
        val state = UiState(
            overlayPermission = true,
            usagePermission = true,
            overlayEnabled = true,
            autoStart = true,
            connection = ConnectionState.ERROR,
            connectionError = "Gateway refused the pairing token.",
            gatewayUrl = "https://oyi.example",
            token = "secret",
            deviceId = "a1b2c3d4e5",
            islandNudgeXDp = 2,
            currentApp = ForegroundApp("com.google.android.youtube", "YouTube", now - 600_000),
            topApps = listOf(
                AppUsage("com.instagram.android", "Instagram", 5_400_000),
                AppUsage("com.google.android.youtube", "YouTube", 2_700_000),
                AppUsage("com.whatsapp", "WhatsApp", 1_300_000),
                AppUsage("com.android.chrome", "Chrome", 600_000),
            ),
        )
        Still {
            OyiTheme {
                MainShell(state = state, level = 0f, tab = 1, onTab = {}, onIslandAction = {}) {
                    SpaceContent(state, SpaceActions())
                }
            }
        }
    }

    @Test
    fun focusAll() = paparazzi.snapshot {
        val labels = mapOf("com.instagram.android" to "Instagram", "com.google.android.youtube" to "YouTube")
        Still {
            OyiTheme {
                MainShell(state = UiState(), level = 0f, tab = 0, onTab = {}, onIslandAction = {}) {
                    FocusContent(
                        state = FocusState(
                            distractions = listOf(
                                Distraction("com.instagram.android", 30),
                                Distraction("com.google.android.youtube", LIMIT_FOCUS_ONLY),
                                Distraction("com.zhiliaoapp.musically", LIMIT_BLOCKED),
                            ),
                            sites = listOf("youtube.com", "x.com", "reddit.com"),
                        ),
                        live = FocusLive(now = now),
                        accessibilityReady = true,
                        notice = "",
                        apps = emptyList(),
                        actions = FocusActions(label = { labels[it] ?: it }),
                    )
                }
            }
        }
    }
}
