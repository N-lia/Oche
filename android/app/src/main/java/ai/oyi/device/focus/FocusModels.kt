package ai.oyi.device.focus

/** Daily allowance of 0 minutes: the app is always blocked. */
const val LIMIT_BLOCKED = 0

/** No daily allowance: the app only matters while a focus session runs. */
const val LIMIT_FOCUS_ONLY = -1

/** An app the user marked as a distraction. */
data class Distraction(
    val packageName: String,
    val dailyLimitMin: Int = LIMIT_FOCUS_ONLY,
) {
    /** Higher means looser. Used to tell tightening from loosening. */
    val looseness: Int
        get() = if (dailyLimitMin < 0) Int.MAX_VALUE else dailyLimitMin
}

/** One task block: the user committed to [task] until [endsAt]. */
data class FocusSession(
    val task: String,
    val startedAt: Long,
    val endsAt: Long,
)

/** Tone of the nudges. Each voice ships with its own offline lines. */
enum class Voice(val label: String) {
    BIG_BROTHER("Big brother"),
    CALM_COACH("Calm coach"),
    DRILL_SERGEANT("Drill sergeant"),
}

/** The monthly lock: once armed, blocks can be added but not loosened. */
data class LockConfig(
    val armed: Boolean = false,
    val armedAt: Long = 0L,
    val emergencyRequestedAt: Long = 0L,
)

/** How long the user may drift before each escalation step. */
data class DriftConfig(
    val firstNudgeSec: Int = 180,
    val secondNudgeSec: Int = 120,
    val lockdownSec: Int = 120,
)

/** Everything the blocker enforces. Lives on the phone; no network needed. */
data class FocusState(
    val distractions: List<Distraction> = emptyList(),
    val sites: List<String> = emptyList(),
    val session: FocusSession? = null,
    val lock: LockConfig = LockConfig(),
    val voice: Voice = Voice.BIG_BROTHER,
    val drift: DriftConfig = DriftConfig(),
) {
    fun distraction(packageName: String): Distraction? =
        distractions.firstOrNull { it.packageName == packageName }
}

/** Foreground time per distraction app for one calendar day. */
data class DayUsage(
    val day: String = "",
    val usedMs: Map<String, Long> = emptyMap(),
)
