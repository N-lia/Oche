package ai.oyi.device.ui.island

import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState

/** How big the island is drawn. */
enum class IslandSize {
    /** A small capsule: link dot, the app in front and its session time. */
    REST,

    /** A wide pill: icon, one line and a live accessory. */
    COMPACT,

    /** A card: badge, title, a line of detail and buttons. */
    EXPANDED,
}

/** The accent a state is drawn in. */
enum class IslandTone { NEUTRAL, ACCENT, LIVE, OK, WARN, BAD }

/** The icon or animation on the left. */
enum class IslandLead { DOT, MIC, SPINNER, TOOL, PEN, ORB, PHONE, CHECK, ALERT, ASK }

/** The live accessory on the right of a compact pill. */
enum class IslandTrail { NONE, TIMER, ELAPSED, BARS, SPINNER, CHECK, STEP, CLOCK }

/**
 * Caleb's expression stickers (res/drawable-nodpi/oyi_face_*.png). One
 * pops up on the island for each state.
 */
enum class IslandFace { NEUTRAL, COOL, THINKING, LAUGHING, TALKING, CODING, IDEA, SLEEPING, SCREENS }

/** A button on an expanded card, or what a gesture on the island does. */
enum class IslandAction {
    APPROVE,
    DENY,
    STOP,
    DISMISS,
    OPEN_APP,

    /** Double tap while OYI speaks: cut the reply short. */
    INTERRUPT,

    /** Tap on the second bubble, or a sideways swipe: bring the other activity forward. */
    SWAP,
}

/**
 * How the face moves for a state. Each state gets its own motion so the
 * island reads at a glance: breathing while it listens, a wobble while it
 * thinks, a typing bob while a tool runs, a pulse while it talks, a bounce
 * when something lands and a shake when it needs you.
 */
enum class IslandMotion { STILL, BREATHE, WOBBLE, TYPE, PULSE, BOUNCE, SHAKE }

/** What is in the idle pill when nothing else is going on. */
data class IslandIdleInfo(
    val appLabel: String = "",
    val sessionMs: Long = 0L,
    val todayMs: Long = -1L,
    val link: ConnectionState = ConnectionState.DISCONNECTED,
    val linkError: String = "",
)

/** Everything the composable needs to draw one frame, decided without Compose. */
data class IslandSpec(
    val size: IslandSize,
    val tone: IslandTone,
    val lead: IslandLead,
    val trail: IslandTrail,
    val title: String,
    val detail: String = "",
    val badge: String = "",
    val actions: List<IslandAction> = emptyList(),
    /** A tap opens the card (compact states with something more to say). */
    val expandable: Boolean = false,
    /** A swipe up or tap closes it. */
    val dismissable: Boolean = false,
    /** The expression shown for this state. */
    val face: IslandFace = IslandFace.NEUTRAL,
    /** How the face moves. */
    val motion: IslandMotion = IslandMotion.STILL,
    /** Work is under way: a light runs around the island's rim. */
    val progressRim: Boolean = false,
    /** What a double tap does, if anything. */
    val doubleTap: IslandAction? = null,
)

/**
 * Maps the runtime's island state to a drawing spec.
 *
 * [expanded] is the user's tap: it opens the detail of a compact state, or
 * the telemetry drawer of the idle pill.
 */
fun islandSpec(state: IslandState, idle: IslandIdleInfo, expanded: Boolean): IslandSpec =
    baseSpec(state, idle, expanded).copy(
        face = faceFor(state, expanded),
        motion = motionFor(state),
        progressRim = showsProgressRim(state),
        doubleTap = doubleTapFor(state),
    )

/** The face's motion for each state (see [IslandMotion]). */
fun motionFor(state: IslandState): IslandMotion = when (state.kind) {
    IslandKind.IDLE -> IslandMotion.STILL
    IslandKind.LISTENING -> IslandMotion.BREATHE
    IslandKind.THINKING -> IslandMotion.WOBBLE
    IslandKind.TOOL -> if (state.toolDone) IslandMotion.BOUNCE else IslandMotion.TYPE
    IslandKind.REPLYING -> IslandMotion.TYPE
    IslandKind.SPEAKING -> IslandMotion.PULSE
    IslandKind.WORKING -> IslandMotion.TYPE
    IslandKind.COMPLETED -> IslandMotion.BOUNCE
    IslandKind.APPROVAL_REQUIRED, IslandKind.ERROR -> IslandMotion.SHAKE
}

/** The rim light runs while something is being worked on. */
fun showsProgressRim(state: IslandState): Boolean = when (state.kind) {
    IslandKind.THINKING, IslandKind.REPLYING, IslandKind.WORKING -> true
    IslandKind.TOOL -> !state.toolDone
    else -> false
}

/** Double tap interrupts a spoken reply, or stops phone control. */
fun doubleTapFor(state: IslandState): IslandAction? = when (state.kind) {
    IslandKind.SPEAKING -> IslandAction.INTERRUPT
    IslandKind.WORKING -> IslandAction.STOP
    else -> null
}

/**
 * Which expression goes with which state: asleep when nothing is going on,
 * hand on chin while thinking, at the laptop while a tool runs, "…" while
 * replying or speaking, shades and screens while driving the phone, a
 * lightbulb when OYI needs a yes, a laugh when a turn lands.
 */
fun faceFor(state: IslandState, expanded: Boolean): IslandFace = when (state.kind) {
    IslandKind.IDLE -> if (expanded) IslandFace.NEUTRAL else IslandFace.SLEEPING
    IslandKind.LISTENING -> IslandFace.NEUTRAL
    IslandKind.THINKING -> IslandFace.THINKING
    IslandKind.TOOL -> IslandFace.CODING
    IslandKind.REPLYING, IslandKind.SPEAKING -> IslandFace.TALKING
    IslandKind.WORKING -> IslandFace.SCREENS
    IslandKind.APPROVAL_REQUIRED -> IslandFace.IDEA
    IslandKind.COMPLETED -> if (state.fromAgent) IslandFace.LAUGHING else IslandFace.COOL
    IslandKind.ERROR -> IslandFace.THINKING
}

private fun baseSpec(state: IslandState, idle: IslandIdleInfo, expanded: Boolean): IslandSpec {
    val tools = state.tools.joinToString(" · ")
    return when (state.kind) {
        IslandKind.IDLE -> if (expanded) {
            IslandSpec(
                size = IslandSize.EXPANDED,
                tone = linkTone(idle.link),
                lead = IslandLead.DOT,
                trail = IslandTrail.NONE,
                title = idle.appLabel.ifBlank { "OYI" },
                detail = idleDetail(idle),
                badge = linkLabel(idle),
                actions = listOf(IslandAction.OPEN_APP),
                expandable = true,
            )
        } else {
            IslandSpec(
                size = IslandSize.REST,
                tone = linkTone(idle.link),
                lead = IslandLead.DOT,
                trail = if (idle.appLabel.isBlank()) IslandTrail.NONE else IslandTrail.CLOCK,
                title = idle.appLabel.ifBlank { "OYI" },
                expandable = true,
            )
        }

        IslandKind.LISTENING -> compact(
            state, expanded,
            tone = IslandTone.LIVE,
            lead = IslandLead.MIC,
            trail = IslandTrail.BARS,
            title = state.title.ifBlank { "Listening" },
            detail = "Talk any time. You can interrupt OYI mid-sentence.",
            badge = "LIVE",
        )

        IslandKind.THINKING -> compact(
            state, expanded,
            tone = IslandTone.ACCENT,
            lead = IslandLead.SPINNER,
            trail = IslandTrail.TIMER,
            title = state.title.ifBlank { "Thinking…" },
            detail = tools,
            badge = "THINKING",
        )

        IslandKind.TOOL -> compact(
            state, expanded,
            tone = IslandTone.ACCENT,
            lead = IslandLead.TOOL,
            trail = if (state.toolDone) IslandTrail.CHECK else IslandTrail.SPINNER,
            title = state.title.ifBlank { "Working…" },
            detail = if (tools.isBlank()) "" else "Tools this turn: $tools",
            badge = "USING A TOOL",
        )

        IslandKind.REPLYING -> compact(
            state, expanded,
            tone = IslandTone.ACCENT,
            lead = IslandLead.PEN,
            trail = IslandTrail.TIMER,
            title = state.title.ifBlank { "Replying…" },
            detail = if (tools.isBlank()) "" else "Tools this turn: $tools",
            badge = "REPLYING",
        )

        IslandKind.SPEAKING -> compact(
            state, expanded,
            tone = IslandTone.ACCENT,
            lead = IslandLead.ORB,
            trail = IslandTrail.BARS,
            title = state.title.ifBlank { "Speaking" },
            detail = "Start talking to interrupt.",
            badge = "SPEAKING",
        )

        IslandKind.WORKING -> IslandSpec(
            size = if (expanded) IslandSize.EXPANDED else IslandSize.COMPACT,
            tone = IslandTone.ACCENT,
            lead = IslandLead.PHONE,
            trail = if (state.step > 0) IslandTrail.STEP else IslandTrail.SPINNER,
            title = state.subtitle.ifBlank { state.title.ifBlank { "Controlling your phone" } },
            detail = state.title,
            badge = if (state.step > 0) "PHONE · STEP ${state.step}" else "CONTROLLING PHONE",
            actions = listOf(IslandAction.STOP),
            expandable = true,
        )

        IslandKind.APPROVAL_REQUIRED -> IslandSpec(
            size = IslandSize.EXPANDED,
            tone = IslandTone.WARN,
            lead = IslandLead.ASK,
            trail = IslandTrail.NONE,
            title = state.title.ifBlank { "Approval required" },
            detail = state.subtitle.ifBlank { "OYI needs your permission to go on." },
            badge = "NEEDS YOU",
            actions = listOf(IslandAction.DENY, IslandAction.APPROVE),
        )

        IslandKind.COMPLETED -> if (state.fromAgent && !expanded) {
            // A chat or voice turn finishing: a quick compact "Done" with the
            // time. A tap shows the tools it used.
            IslandSpec(
                size = IslandSize.COMPACT,
                tone = IslandTone.OK,
                lead = IslandLead.CHECK,
                trail = if (state.elapsedMs > 0) IslandTrail.ELAPSED else IslandTrail.NONE,
                title = state.title.ifBlank { "Done" },
                detail = state.subtitle,
                expandable = state.subtitle.isNotBlank(),
                dismissable = true,
            )
        } else {
            IslandSpec(
                size = IslandSize.EXPANDED,
                tone = IslandTone.OK,
                lead = IslandLead.CHECK,
                trail = if (state.elapsedMs > 0) IslandTrail.ELAPSED else IslandTrail.NONE,
                title = state.title.ifBlank { "Done" },
                detail = state.subtitle,
                badge = "DONE",
                actions = if (state.fromAgent) emptyList() else listOf(IslandAction.DISMISS),
                dismissable = true,
            )
        }

        IslandKind.ERROR -> IslandSpec(
            size = IslandSize.EXPANDED,
            tone = IslandTone.BAD,
            lead = IslandLead.ALERT,
            trail = IslandTrail.NONE,
            title = state.title.ifBlank { "Something went wrong" },
            detail = state.subtitle,
            badge = "HEADS UP",
            actions = listOf(IslandAction.DISMISS),
            dismissable = true,
        )
    }
}

private fun compact(
    state: IslandState,
    expanded: Boolean,
    tone: IslandTone,
    lead: IslandLead,
    trail: IslandTrail,
    title: String,
    detail: String,
    badge: String,
): IslandSpec {
    val canExpand = detail.isNotBlank()
    return IslandSpec(
        size = if (expanded && canExpand) IslandSize.EXPANDED else IslandSize.COMPACT,
        tone = tone,
        lead = lead,
        trail = trail,
        title = title,
        detail = detail,
        badge = badge,
        expandable = canExpand,
    )
}

private fun linkTone(link: ConnectionState): IslandTone = when (link) {
    ConnectionState.CONNECTED -> IslandTone.LIVE
    ConnectionState.CONNECTING -> IslandTone.WARN
    ConnectionState.ERROR -> IslandTone.BAD
    ConnectionState.DISCONNECTED -> IslandTone.NEUTRAL
}

private fun linkLabel(idle: IslandIdleInfo): String = when (idle.link) {
    ConnectionState.CONNECTED -> "LINKED"
    ConnectionState.CONNECTING -> "CONNECTING"
    ConnectionState.ERROR -> "LINK ERROR"
    ConnectionState.DISCONNECTED -> "OFFLINE"
}

private fun idleDetail(idle: IslandIdleInfo): String {
    val parts = mutableListOf<String>()
    if (idle.appLabel.isNotBlank()) parts += "This session ${formatDuration(idle.sessionMs)}"
    if (idle.todayMs >= 0) parts += "Today ${formatDuration(idle.todayMs)}"
    if (idle.link == ConnectionState.ERROR && idle.linkError.isNotBlank()) parts += idle.linkError
    return parts.joinToString(" · ").ifBlank { "Nothing running. Tap and hold to open OYI." }
}

/** "12s", "4m", "1h 05m". */
fun formatDuration(millis: Long): String {
    val totalSeconds = (millis / 1000).coerceAtLeast(0)
    val hours = totalSeconds / 3600
    val minutes = (totalSeconds % 3600) / 60
    val seconds = totalSeconds % 60
    return when {
        hours > 0 -> "${hours}h ${minutes.toString().padStart(2, '0')}m"
        minutes > 0 -> "${minutes}m"
        else -> "${seconds}s"
    }
}

/** The live timer: "3.4s", then "42s", then "2:05", then "1h 05m". */
fun formatTenths(millis: Long): String {
    val ms = millis.coerceAtLeast(0)
    val seconds = ms / 1000
    return when {
        ms < 10_000 -> "${ms / 1000}.${(ms / 100) % 10}s"
        seconds < 60 -> "${seconds}s"
        seconds < 3600 -> "${seconds / 60}:${(seconds % 60).toString().padStart(2, '0')}"
        else -> formatDuration(ms)
    }
}
