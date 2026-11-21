package ai.oyi.device.focus

/**
 * The offline nudge lines. Each voice gets firmer with the drift stage; the
 * AI can later replace these with fresh ones, but the phone never needs it to.
 */
object NudgeVoice {

    data class Line(val title: String, val body: String)

    fun nudge(voice: Voice, stage: Int, task: String): Line {
        val name = task.ifBlank { "your task" }
        return when (voice) {
            Voice.BIG_BROTHER -> when (stage) {
                DriftState.STAGE_GENTLE -> Line(
                    "Hey, you wandered off",
                    "No stress. \"$name\" is still waiting for you.",
                )
                DriftState.STAGE_FIRM -> Line(
                    "Come on, you know better",
                    "You are skipping \"$name\". Close this and get back to it.",
                )
                else -> Line(
                    "Okay, I am stepping in",
                    "Distractions are locked until \"$name\" is done. You asked me to.",
                )
            }
            Voice.CALM_COACH -> when (stage) {
                DriftState.STAGE_GENTLE -> Line(
                    "A small drift",
                    "Return to \"$name\" when this breath ends.",
                )
                DriftState.STAGE_FIRM -> Line(
                    "Still drifting",
                    "\"$name\" is the plan. Step back into it now.",
                )
                else -> Line(
                    "Lockdown",
                    "Distractions are closed until \"$name\" is done.",
                )
            }
            Voice.DRILL_SERGEANT -> when (stage) {
                DriftState.STAGE_GENTLE -> Line(
                    "Eyes front",
                    "You are off task. \"$name\". Move.",
                )
                DriftState.STAGE_FIRM -> Line(
                    "Second warning",
                    "Drop the phone games. \"$name\", now.",
                )
                else -> Line(
                    "Locked down",
                    "You had two warnings. Nothing opens until \"$name\" is done.",
                )
            }
        }
    }

    /** The headline on the block screen. */
    fun blocked(voice: Voice, reason: BlockReason, task: String): String {
        val name = task.ifBlank { "what matters" }
        return when (reason) {
            BlockReason.LOCKDOWN -> when (voice) {
                Voice.BIG_BROTHER -> "Not now.\nFinish \"$name\" first."
                Voice.CALM_COACH -> "This can wait.\n\"$name\" cannot."
                Voice.DRILL_SERGEANT -> "Denied.\nBack to \"$name\"."
            }
            BlockReason.LIMIT -> when (voice) {
                Voice.BIG_BROTHER -> "That is today's share.\nSee you tomorrow."
                Voice.CALM_COACH -> "Today's allowance\nis spent."
                Voice.DRILL_SERGEANT -> "Time is up.\nNo extensions."
            }
            BlockReason.ALWAYS, BlockReason.SITE -> when (voice) {
                Voice.BIG_BROTHER -> "You blocked this\nfor a reason."
                Voice.CALM_COACH -> "This one stays\nclosed."
                Voice.DRILL_SERGEANT -> "Off limits.\nYour own orders."
            }
        }
    }
}
