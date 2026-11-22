package ai.oyi.device.snapshots

import ai.oyi.device.focus.Distraction
import ai.oyi.device.focus.FocusLive
import ai.oyi.device.focus.FocusSession
import ai.oyi.device.focus.FocusState
import ai.oyi.device.focus.LIMIT_BLOCKED
import ai.oyi.device.focus.LIMIT_FOCUS_ONLY
import ai.oyi.device.focus.LockPhase
import ai.oyi.device.focus.LockStatus
import ai.oyi.device.model.AppUsage
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.ForegroundApp
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import ai.oyi.device.ui.ChatActions
import ai.oyi.device.ui.ChatContent
import ai.oyi.device.ui.ChatMessage
import ai.oyi.device.ui.ChatRole
import ai.oyi.device.ui.ChatState
import ai.oyi.device.ui.FocusActions
import ai.oyi.device.ui.FocusContent
import ai.oyi.device.ui.ScreenTime
import ai.oyi.device.ui.IslandFitContent
import ai.oyi.device.update.Release
import ai.oyi.device.update.UpdateState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import ai.oyi.device.ui.MainShell
import ai.oyi.device.ui.SpaceActions
import ai.oyi.device.ui.SpaceContent
import ai.oyi.device.ui.UiState
import ai.oyi.device.ui.theme.OyiTheme
import androidx.compose.runtime.Composable
import app.cash.paparazzi.DeviceConfig
import app.cash.paparazzi.Paparazzi
import org.junit.Rule
import org.junit.Test

/**
 * Whole screens, phone-sized, so the design can be reviewed without a
 * phone. CI publishes the images to the snapshots/pr-<n> branch.
 */
class ScreensSnapshotTest {

    @get:Rule
    val paparazzi = Paparazzi(
        deviceConfig = DeviceConfig.PIXEL_5,
        theme = "android:Theme.Material.NoActionBar",
    )

    private val now = 1_763_700_000_000L
    private val labels = mapOf(
        "com.instagram.android" to "Instagram",
        "com.google.android.youtube" to "YouTube",
        "com.zhiliaoapp.musically" to "TikTok",
    )

    private val space = UiState(
        overlayPermission = true,
        usagePermission = true,
        accessibilityEnabled = false,
        overlayEnabled = true,
        connection = ConnectionState.CONNECTED,
        gatewayUrl = "https://oyi.example",
        token = "secret",
        deviceId = "a1b2c3d4e5",
        currentApp = ForegroundApp("com.google.android.youtube", "YouTube", now - 600_000),
        topApps = listOf(
            AppUsage("com.instagram.android", "Instagram", 5_400_000),
            AppUsage("com.google.android.youtube", "YouTube", 2_700_000),
            AppUsage("com.whatsapp", "WhatsApp", 1_300_000),
        ),
    )

    @Composable
    private fun Frame(tab: Int, state: UiState = space, content: @Composable () -> Unit) {
        Still {
            OyiTheme {
                MainShell(state = state, level = 0.4f, tab = tab, onTab = {}, onIslandAction = {}) { content() }
            }
        }
    }

    @Test
    fun focusIdle() = paparazzi.snapshot {
        Frame(tab = 1) {
            FocusContent(
                state = FocusState(
                    distractions = listOf(
                        Distraction("com.instagram.android", 30),
                        Distraction("com.google.android.youtube", LIMIT_FOCUS_ONLY),
                        Distraction("com.zhiliaoapp.musically", LIMIT_BLOCKED),
                    ),
                    sites = listOf("youtube.com", "x.com"),
                ),
                live = FocusLive(now = now, usedToday = mapOf("com.instagram.android" to 1_260_000L)),
                accessibilityReady = false,
                notice = "",
                apps = emptyList(),
                actions = FocusActions(label = { labels[it] ?: it }),
                screenTime = ScreenTime(space.topApps, space.currentApp),
            )
        }
    }

    @Test
    fun focusInSession() = paparazzi.snapshot {
        Frame(tab = 1, state = space.copy(island = IslandState(kind = IslandKind.THINKING, title = "Thinking…", startedAt = now))) {
            FocusContent(
                state = FocusState(
                    session = FocusSession("Write the essay intro", startedAt = now - 900_000, endsAt = now + 2_100_000),
                ),
                live = FocusLive(now = now, lock = LockStatus(LockPhase.LOCKED, until = now + 9 * 86_400_000L)),
                accessibilityReady = true,
                notice = "",
                apps = emptyList(),
                actions = FocusActions(label = { labels[it] ?: it }),
            )
        }
    }

    @Test
    fun space() = paparazzi.snapshot {
        Frame(tab = 2) { SpaceContent(space, SpaceActions()) }
    }

    @Test
    fun chat() = paparazzi.snapshot {
        Frame(tab = 0, state = space.copy(island = IslandState(kind = IslandKind.SPEAKING, title = "Speaking", startedAt = now))) {
            ChatContent(
                state = ChatState(
                    messages = listOf(
                        ChatMessage(role = ChatRole.USER, text = "How much time have I used today?"),
                        ChatMessage(
                            role = ChatRole.AGENT,
                            text = "About 2h 40m. Instagram is most of it at 1h 30m, then YouTube at 45m.",
                        ),
                        ChatMessage(role = ChatRole.USER, text = "Block Instagram until 6"),
                    ),
                    liveActive = true,
                    liveStatus = "Listening…",
                ),
                level = 0.5f,
                actions = ChatActions(),
            )
        }
    }

    @Test
    fun chatEmpty() = paparazzi.snapshot {
        Frame(tab = 0) { ChatContent(ChatState(), 0f, ChatActions()) }
    }

    @Test
    fun chatNeedsSetup() = paparazzi.snapshot {
        Frame(tab = 0, state = space.copy(connection = ConnectionState.DISCONNECTED, gatewayUrl = "")) {
            ChatContent(ChatState(), 0f, ChatActions(), needsSetup = true)
        }
    }

    /** The same failure three times folds into one quiet line, and no error bar repeats it. */
    @Test
    fun chatFailures() = paparazzi.snapshot {
        val failure = "Couldn't reach OYI. Gateway is not configured."
        Frame(tab = 0) {
            ChatContent(
                state = ChatState(
                    messages = listOf(
                        ChatMessage(role = ChatRole.USER, text = "hello"),
                        ChatMessage(role = ChatRole.SYSTEM, text = failure),
                        ChatMessage(role = ChatRole.USER, text = "hello?"),
                        ChatMessage(role = ChatRole.SYSTEM, text = failure),
                        ChatMessage(role = ChatRole.SYSTEM, text = failure),
                    ),
                    error = "Gateway is not configured.",
                ),
                level = 0f,
                actions = ChatActions(),
            )
        }
    }

    @Test
    fun spaceFresh() = paparazzi.snapshot {
        val fresh = UiState()
        Frame(tab = 2, state = fresh) { SpaceContent(fresh, SpaceActions()) }
    }

    @Test
    fun spaceUpdateReady() = paparazzi.snapshot {
        val update = UpdateState.Available(
            Release(build = 214, apkUrl = "", sizeBytes = 0, notes = "Bouncier island morphs, live island fitting, screen time in Focus."),
        )
        Frame(tab = 2) { SpaceContent(space, SpaceActions(), update, "0.1.210 (build 210)") }
    }

    /** The live fit screen, over a stand-in for whatever is behind it. */
    @Test
    fun islandFit() = paparazzi.snapshot {
        Still {
            OyiTheme {
                androidx.compose.foundation.layout.Box(
                    androidx.compose.ui.Modifier
                        .fillMaxSize()
                        .background(androidx.compose.ui.graphics.Color(0xFF2A2A30)),
                ) {
                    IslandFitContent(x = 6, y = -3, live = true, onChange = { _, _ -> }, onTurnOn = {}, onDone = {})
                }
            }
        }
    }
}
