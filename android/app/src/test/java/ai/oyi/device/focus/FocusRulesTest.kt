package ai.oyi.device.focus

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class FocusRulesTest {

    private val zone: ZoneId = ZoneId.of("UTC")

    private fun at(date: String, hour: Int = 12): Long =
        LocalDate.parse(date).atStartOfDay(zone).toInstant().toEpochMilli() + hour * 3_600_000L

    // ---- monthly lock -------------------------------------------------

    @Test
    fun lockIsOffUntilArmed() {
        val status = MonthlyLock.status(LockConfig(), at("2026-11-13"), verified = true, zone = zone)
        assertEquals(LockPhase.OFF, status.phase)
        assertTrue(status.canLoosen)
    }

    @Test
    fun armedLockStaysShutMidMonth() {
        val lock = LockConfig(armed = true, armedAt = at("2026-11-13"))
        val status = MonthlyLock.status(lock, at("2026-11-20"), verified = true, zone = zone)
        assertEquals(LockPhase.LOCKED, status.phase)
        assertFalse(status.canLoosen)
        assertEquals(at("2026-12-01", hour = 0), status.until)
    }

    @Test
    fun windowOpensOnTheFirstOfTheMonth() {
        val lock = LockConfig(armed = true, armedAt = at("2026-11-13"))
        val status = MonthlyLock.status(lock, at("2026-12-01"), verified = true, zone = zone)
        assertEquals(LockPhase.WINDOW_OPEN, status.phase)
        assertTrue(status.canLoosen)
        assertEquals(at("2026-12-02", hour = 0), status.until)
    }

    @Test
    fun windowClosesAfterTheFirst() {
        val lock = LockConfig(armed = true, armedAt = at("2026-11-13"))
        val status = MonthlyLock.status(lock, at("2026-12-02", hour = 1), verified = true, zone = zone)
        assertEquals(LockPhase.LOCKED, status.phase)
    }

    @Test
    fun armingDuringTheWindowDoesNotLeaveItOpen() {
        val lock = LockConfig(armed = true, armedAt = at("2026-12-01", hour = 9))
        val status = MonthlyLock.status(lock, at("2026-12-01", hour = 10), verified = true, zone = zone)
        assertEquals(LockPhase.LOCKED, status.phase)
    }

    @Test
    fun windowNeedsAVerifiedClock() {
        val lock = LockConfig(armed = true, armedAt = at("2026-11-13"))
        val status = MonthlyLock.status(lock, at("2026-12-01"), verified = false, zone = zone)
        assertEquals(LockPhase.LOCKED, status.phase)
        assertTrue(status.needsSync)
        assertFalse(status.canLoosen)
    }

    @Test
    fun emergencyExitWaitsADayThenOpensForAnHour() {
        val requested = at("2026-11-14")
        val lock = LockConfig(armed = true, armedAt = at("2026-11-13"), emergencyRequestedAt = requested)
        val hour = 3_600_000L

        val waiting = MonthlyLock.status(lock, requested + 23 * hour, verified = false, zone = zone)
        assertEquals(LockPhase.EMERGENCY_WAIT, waiting.phase)
        assertFalse(waiting.canLoosen)

        val open = MonthlyLock.status(lock, requested + 24 * hour + 1, verified = false, zone = zone)
        assertEquals(LockPhase.EMERGENCY_OPEN, open.phase)
        assertTrue(open.canLoosen)

        val after = MonthlyLock.status(lock, requested + 25 * hour + 1, verified = false, zone = zone)
        assertEquals(LockPhase.LOCKED, after.phase)
        assertTrue(MonthlyLock.emergencyExpired(lock, requested + 25 * hour + 1))
    }

    // ---- tightening vs loosening --------------------------------------

    private val base = FocusState(
        distractions = listOf(Distraction("yt", 30), Distraction("ig", LIMIT_BLOCKED)),
        sites = listOf("reddit.com"),
        lock = LockConfig(armed = true, armedAt = 1L),
    )

    @Test
    fun addingAndTighteningIsNeverLoosening() {
        assertFalse(MonthlyLock.isLoosening(base, base.copy(distractions = base.distractions + Distraction("tt", 15))))
        assertFalse(MonthlyLock.isLoosening(base, base.copy(sites = base.sites + "x.com")))
        val tighter = base.copy(distractions = listOf(Distraction("yt", 15), Distraction("ig", LIMIT_BLOCKED)))
        assertFalse(MonthlyLock.isLoosening(base, tighter))
        assertFalse(MonthlyLock.isLoosening(base, base.copy(voice = Voice.DRILL_SERGEANT)))
        assertFalse(MonthlyLock.isLoosening(base, base.copy(session = FocusSession("write", 0L, 10L))))
    }

    @Test
    fun removingOrRelaxingIsLoosening() {
        assertTrue(MonthlyLock.isLoosening(base, base.copy(distractions = base.distractions.drop(1))))
        val raised = base.copy(distractions = listOf(Distraction("yt", 60), Distraction("ig", LIMIT_BLOCKED)))
        assertTrue(MonthlyLock.isLoosening(base, raised))
        val unblocked = base.copy(distractions = listOf(Distraction("yt", 30), Distraction("ig", 15)))
        assertTrue(MonthlyLock.isLoosening(base, unblocked))
        val focusOnly = base.copy(distractions = listOf(Distraction("yt", LIMIT_FOCUS_ONLY), Distraction("ig", LIMIT_BLOCKED)))
        assertTrue(MonthlyLock.isLoosening(base, focusOnly))
        assertTrue(MonthlyLock.isLoosening(base, base.copy(sites = emptyList())))
        assertTrue(MonthlyLock.isLoosening(base, base.copy(lock = LockConfig())))
        assertTrue(MonthlyLock.isLoosening(base, base.copy(drift = DriftConfig(firstNudgeSec = 600))))
    }

    // ---- block decisions ----------------------------------------------

    @Test
    fun appVerdicts() {
        val state = FocusState(
            distractions = listOf(
                Distraction("blocked", LIMIT_BLOCKED),
                Distraction("limited", 30),
                Distraction("focus", LIMIT_FOCUS_ONLY),
            ),
        )
        assertNull(FocusEngine.appVerdict(state, "other", 0L, lockdown = false))
        assertEquals(BlockReason.ALWAYS, FocusEngine.appVerdict(state, "blocked", 0L, lockdown = false))
        assertNull(FocusEngine.appVerdict(state, "limited", 29 * 60_000L, lockdown = false))
        assertEquals(BlockReason.LIMIT, FocusEngine.appVerdict(state, "limited", 30 * 60_000L, lockdown = false))
        assertNull(FocusEngine.appVerdict(state, "focus", 99 * 60_000L, lockdown = false))

        // Lockdown only bites while a session is running.
        assertNull(FocusEngine.appVerdict(state, "focus", 0L, lockdown = true))
        val inSession = state.copy(session = FocusSession("write", 0L, 10L))
        assertEquals(BlockReason.LOCKDOWN, FocusEngine.appVerdict(inSession, "focus", 0L, lockdown = true))
        assertNull(FocusEngine.appVerdict(inSession, "other", 0L, lockdown = true))
    }

    @Test
    fun siteMatching() {
        val state = FocusState(sites = listOf("youtube.com"))
        assertEquals(BlockReason.SITE, FocusEngine.siteVerdict(state, "youtube.com"))
        assertEquals(BlockReason.SITE, FocusEngine.siteVerdict(state, "music.youtube.com"))
        assertNull(FocusEngine.siteVerdict(state, "notyoutube.com"))
        assertNull(FocusEngine.siteVerdict(state, "example.org"))
    }

    @Test
    fun hostParsing() {
        assertEquals("youtube.com", BrowserUrls.hostOf("https://www.youtube.com/watch?v=1"))
        assertEquals("youtube.com", BrowserUrls.hostOf("m.youtube.com/shorts"))
        assertEquals("youtube.com", BrowserUrls.hostOf("  YouTube.com  "))
        assertEquals("example.co.uk", BrowserUrls.hostOf("http://example.co.uk:8080/a#b"))
        assertEquals("medium.com", BrowserUrls.hostOf("medium.com"))
        assertNull(BrowserUrls.hostOf("how to focus"))
        assertNull(BrowserUrls.hostOf("localhost"))
        assertNull(BrowserUrls.hostOf(""))
    }

    // ---- drift escalation ---------------------------------------------

    @Test
    fun driftEscalatesNudgeNudgeLockdown() {
        val config = DriftConfig(firstNudgeSec = 180, secondNudgeSec = 120, lockdownSec = 120)
        var drift = DriftState()
        fun spend(seconds: Int, inDistraction: Boolean) {
            repeat(seconds) { drift = drift.advance(inDistraction, 1000L, config) }
        }

        spend(179, inDistraction = true)
        assertEquals(0, drift.stage)
        spend(1, inDistraction = true)
        assertEquals(DriftState.STAGE_GENTLE, drift.stage)
        spend(120, inDistraction = true)
        assertEquals(DriftState.STAGE_FIRM, drift.stage)
        spend(120, inDistraction = true)
        assertEquals(DriftState.STAGE_LOCKDOWN, drift.stage)

        // Lockdown holds even after the user leaves the app.
        spend(600, inDistraction = false)
        assertEquals(DriftState.STAGE_LOCKDOWN, drift.stage)
    }

    @Test
    fun stayingAwayForgivesADrift() {
        val config = DriftConfig()
        var drift = DriftState()
        repeat(200) { drift = drift.advance(true, 1000L, config) }
        assertEquals(DriftState.STAGE_GENTLE, drift.stage)

        // A short break does not reset the count.
        repeat(60) { drift = drift.advance(false, 1000L, config) }
        assertEquals(DriftState.STAGE_GENTLE, drift.stage)
        repeat(100) { drift = drift.advance(true, 1000L, config) }
        assertEquals(DriftState.STAGE_FIRM, drift.stage)

        // Two clean minutes do.
        repeat(120) { drift = drift.advance(false, 1000L, config) }
        assertEquals(DriftState(), drift)
    }

    // ---- the lock's clock ---------------------------------------------

    @Test
    fun clockIgnoresThePhoneClockWithinABoot() {
        val anchor = ClockAnchor(wall = 1_000_000L, elapsed = 5_000L, boot = 7, verified = true)
        val now = TrustedTime.resolve(anchor, highWater = 1_000_000L, elapsedNow = 65_000L, bootNow = 7, wallNow = 99_999_999_999L)
        assertEquals(1_060_000L, now.ms)
        assertTrue(now.verified)
    }

    @Test
    fun clockFallsBackToALowerBoundAfterReboot() {
        val anchor = ClockAnchor(wall = 1_000_000L, elapsed = 5_000L, boot = 7, verified = true)
        val now = TrustedTime.resolve(anchor, highWater = 1_500_000L, elapsedNow = 2_000L, bootNow = 8, wallNow = 99_999_999_999L)
        assertEquals(1_500_000L, now.ms)
        assertFalse(now.verified)

        // A boot counter that did not move is still caught by the uptime reset.
        val sameCount = TrustedTime.resolve(anchor, highWater = 1_500_000L, elapsedNow = 2_000L, bootNow = 7, wallNow = 0L)
        assertEquals(1_500_000L, sameCount.ms)
        assertFalse(sameCount.verified)
    }

    @Test
    fun clockUsesTheWallClockBeforeTheFirstSync() {
        val now = TrustedTime.resolve(null, highWater = 0L, elapsedNow = 10L, bootNow = 1, wallNow = 42L)
        assertEquals(42L, now.ms)
        assertFalse(now.verified)
    }
}
