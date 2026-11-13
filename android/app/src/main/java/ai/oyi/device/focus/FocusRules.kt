package ai.oyi.device.focus

import java.time.Instant
import java.time.ZoneId

/*
 * Pure decision logic for the blocker. Nothing here touches Android or the
 * network, so every rule can be unit tested and none of it can be skipped by
 * going offline.
 */

enum class LockPhase { OFF, LOCKED, WINDOW_OPEN, EMERGENCY_WAIT, EMERGENCY_OPEN }

/**
 * Where the monthly lock stands right now.
 *
 * [until] is when the current phase ends: for LOCKED the next window opening,
 * for the open phases the closing time, for EMERGENCY_WAIT the opening time.
 */
data class LockStatus(
    val phase: LockPhase,
    val until: Long = 0L,
    /** The window would be open, but the clock has not been checked online. */
    val needsSync: Boolean = false,
) {
    val canLoosen: Boolean
        get() = phase == LockPhase.OFF ||
            phase == LockPhase.WINDOW_OPEN ||
            phase == LockPhase.EMERGENCY_OPEN
}

object MonthlyLock {
    const val EMERGENCY_WAIT_MS = 24L * 60L * 60L * 1000L
    const val EMERGENCY_OPEN_MS = 60L * 60L * 1000L

    /**
     * The window is the first day of each month. A lock armed during that day
     * stays shut until the next month, so arming is never a no-op.
     */
    fun status(
        lock: LockConfig,
        now: Long,
        verified: Boolean,
        zone: ZoneId = ZoneId.systemDefault(),
    ): LockStatus {
        if (!lock.armed) return LockStatus(LockPhase.OFF)

        if (lock.emergencyRequestedAt > 0L) {
            val opens = lock.emergencyRequestedAt + EMERGENCY_WAIT_MS
            val closes = opens + EMERGENCY_OPEN_MS
            if (now < opens) return LockStatus(LockPhase.EMERGENCY_WAIT, opens)
            if (now < closes) return LockStatus(LockPhase.EMERGENCY_OPEN, closes)
        }

        val monthStart = Instant.ofEpochMilli(now).atZone(zone).toLocalDate().withDayOfMonth(1)
        val windowStart = monthStart.atStartOfDay(zone).toInstant().toEpochMilli()
        val windowEnd = monthStart.plusDays(1).atStartOfDay(zone).toInstant().toEpochMilli()
        if (now in windowStart until windowEnd && lock.armedAt < windowStart) {
            return if (verified) {
                LockStatus(LockPhase.WINDOW_OPEN, windowEnd)
            } else {
                LockStatus(LockPhase.LOCKED, windowEnd, needsSync = true)
            }
        }
        val nextStart = monthStart.plusMonths(1).atStartOfDay(zone).toInstant().toEpochMilli()
        return LockStatus(LockPhase.LOCKED, nextStart)
    }

    /** True once an emergency exit has been used up or ignored. */
    fun emergencyExpired(lock: LockConfig, now: Long): Boolean =
        lock.emergencyRequestedAt > 0L &&
            now >= lock.emergencyRequestedAt + EMERGENCY_WAIT_MS + EMERGENCY_OPEN_MS

    /**
     * Does going from [old] to [new] make the blocker weaker?
     *
     * Adding apps or sites and lowering allowances is always fine. Removing
     * them, raising allowances, slowing the escalation or disarming the lock
     * is loosening, and only allowed while [LockStatus.canLoosen].
     */
    fun isLoosening(old: FocusState, new: FocusState): Boolean {
        val after = new.distractions.associateBy { it.packageName }
        for (before in old.distractions) {
            val now = after[before.packageName] ?: return true
            if (now.looseness > before.looseness) return true
        }
        if (!new.sites.containsAll(old.sites)) return true
        if (old.lock.armed && !new.lock.armed) return true
        if (new.drift.firstNudgeSec > old.drift.firstNudgeSec) return true
        if (new.drift.secondNudgeSec > old.drift.secondNudgeSec) return true
        if (new.drift.lockdownSec > old.drift.lockdownSec) return true
        return false
    }
}

enum class BlockReason { ALWAYS, LIMIT, LOCKDOWN, SITE }

object FocusEngine {

    /** Why [packageName] must be blocked right now, or null to allow it. */
    fun appVerdict(
        state: FocusState,
        packageName: String,
        usedTodayMs: Long,
        lockdown: Boolean,
    ): BlockReason? {
        val distraction = state.distraction(packageName) ?: return null
        if (distraction.dailyLimitMin == LIMIT_BLOCKED) return BlockReason.ALWAYS
        if (lockdown && state.session != null) return BlockReason.LOCKDOWN
        if (distraction.dailyLimitMin > 0 &&
            usedTodayMs >= distraction.dailyLimitMin * 60_000L
        ) {
            return BlockReason.LIMIT
        }
        return null
    }

    fun siteVerdict(state: FocusState, host: String): BlockReason? =
        if (state.sites.any { BrowserUrls.matches(host, it) }) BlockReason.SITE else null
}

/**
 * Drift = time spent in distraction apps during a focus session.
 *
 * Stage 0 is on track, 1 a gentle nudge, 2 a firm nudge, 3 lockdown. Staying
 * away from distractions for [RESET_AFTER_MS] forgives the episode; lockdown
 * lasts until the session ends.
 */
data class DriftState(
    val driftMs: Long = 0L,
    val awayMs: Long = 0L,
    val stage: Int = 0,
) {
    fun advance(inDistraction: Boolean, elapsedMs: Long, config: DriftConfig): DriftState {
        if (stage >= STAGE_LOCKDOWN) return this
        if (!inDistraction) {
            val away = awayMs + elapsedMs
            return if (away >= RESET_AFTER_MS) DriftState() else copy(awayMs = away)
        }
        val drift = driftMs + elapsedMs
        val first = config.firstNudgeSec * 1000L
        val second = first + config.secondNudgeSec * 1000L
        val lockdown = second + config.lockdownSec * 1000L
        val reached = when {
            drift >= lockdown -> STAGE_LOCKDOWN
            drift >= second -> STAGE_FIRM
            drift >= first -> STAGE_GENTLE
            else -> 0
        }
        return DriftState(driftMs = drift, awayMs = 0L, stage = maxOf(stage, reached))
    }

    companion object {
        const val STAGE_GENTLE = 1
        const val STAGE_FIRM = 2
        const val STAGE_LOCKDOWN = 3
        const val RESET_AFTER_MS = 120_000L
    }
}

/** A point on the tamper-resistant clock. */
data class ClockAnchor(
    val wall: Long,
    val elapsed: Long,
    val boot: Int,
    val verified: Boolean,
)

data class TrustedNow(val ms: Long, val verified: Boolean)

object TrustedTime {
    /**
     * The current time, without trusting the phone's settable clock.
     *
     * Within one boot the monotonic clock carries the anchor forward, so
     * changing the date in Settings has no effect. After a reboot only a
     * lower bound is known (the last time seen), which can delay an unlock
     * but never bring it forward. With no anchor yet, the wall clock is all
     * there is.
     */
    fun resolve(
        anchor: ClockAnchor?,
        highWater: Long,
        elapsedNow: Long,
        bootNow: Int,
        wallNow: Long,
    ): TrustedNow {
        if (anchor == null) return TrustedNow(wallNow, verified = false)
        val sameBoot = anchor.boot == bootNow && elapsedNow >= anchor.elapsed
        if (sameBoot) {
            return TrustedNow(anchor.wall + (elapsedNow - anchor.elapsed), anchor.verified)
        }
        return TrustedNow(maxOf(highWater, anchor.wall), verified = false)
    }
}
