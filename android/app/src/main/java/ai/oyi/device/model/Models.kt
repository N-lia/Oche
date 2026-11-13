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

/** Visual mode of the OYI Dynamic Island overlay and in-app banner. */
enum class IslandKind {
    IDLE,
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
)
