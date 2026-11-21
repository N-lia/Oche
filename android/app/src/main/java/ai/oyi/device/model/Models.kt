package ai.oyi.device.model

/** Connection state of the outbound WebSocket to the OYI gateway. */
enum class ConnectionState {
    DISCONNECTED,
    CONNECTING,
    CONNECTED,
    ERROR,
}

/** Everything the user configured in the OYI app. */
data class DeviceSettings(
    val gatewayUrl: String = "",
    val token: String = "",
    val overlayEnabled: Boolean = false,
    val autoStart: Boolean = false,
    val deviceId: String = "",
    /** Calibration of the island over the camera, in dp. */
    val islandNudgeXDp: Int = 0,
    val islandNudgeYDp: Int = 0,
    /** Material You: take the accent from the wallpaper instead of OYI orange. */
    val wallpaperColors: Boolean = false,
    /** Mirror busy states as an Android 16 Live Update when the island cannot show. */
    val liveUpdates: Boolean = true,
)

/** The app currently in the foreground. */
data class ForegroundApp(
    val packageName: String,
    val label: String,
    val since: Long,
)

/** Total foreground time for one app on one day. */
data class AppUsage(
    val packageName: String,
    val label: String,
    val totalMs: Long,
)

/** One foreground session of an app. */
data class UsageSession(
    val packageName: String,
    val startedAt: Long,
    val endedAt: Long,
    val durationMs: Long,
)

/** Result of executing one UI action on the phone. */
data class ActionResult(
    val ok: Boolean,
    val message: String = "",
)

/**
 * What the OYI Dynamic Island is showing.
 *
 * The first group follows a chat or live-voice turn; the second follows a
 * phone-control task (and focus nudges, which reuse ERROR).
 */
enum class IslandKind {
    IDLE,

    // Agent turn (chat or live voice)
    LISTENING,
    THINKING,
    TOOL,
    REPLYING,
    SPEAKING,

    // Phone control, focus and alerts
    WORKING,
    COMPLETED,
    APPROVAL_REQUIRED,
    ERROR,
}

/** Live state rendered by the Dynamic Island. */
data class IslandState(
    val kind: IslandKind = IslandKind.IDLE,
    val title: String = "",
    val subtitle: String = "",
    val step: Int = 0,
    val commandId: String = "",
    val updatedAt: Long = System.currentTimeMillis(),
    /** When the current busy stretch began; drives the live timer (0 = none). */
    val startedAt: Long = 0L,
    /** How long the finished turn took, shown on COMPLETED (0 = unknown). */
    val elapsedMs: Long = 0L,
    /** Tools the agent used in this turn, in order. */
    val tools: List<String> = emptyList(),
    /** TOOL only: the tool has finished. */
    val toolDone: Boolean = false,
    /** Set by a chat or live-voice turn (as opposed to phone control or focus). */
    val fromAgent: Boolean = false,
)
