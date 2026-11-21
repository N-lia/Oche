package ai.oyi.device.ui.island

import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class IslandSpecTest {

    private val idle = IslandIdleInfo(appLabel = "YouTube", sessionMs = 125_000, link = ConnectionState.CONNECTED)

    @Test
    fun idleRestsAsASmallPillWithTheAppInFront() {
        val spec = islandSpec(IslandState(), idle, expanded = false)
        assertEquals(IslandSize.REST, spec.size)
        assertEquals("YouTube", spec.title)
        assertEquals(IslandTrail.CLOCK, spec.trail)
        assertEquals(IslandTone.LIVE, spec.tone)
    }

    @Test
    fun tappingIdleOpensTheTelemetryCard() {
        val spec = islandSpec(IslandState(), idle.copy(todayMs = 3_900_000), expanded = true)
        assertEquals(IslandSize.EXPANDED, spec.size)
        assertEquals("This session 2m · Today 1h 05m", spec.detail)
        assertEquals(listOf(IslandAction.OPEN_APP), spec.actions)
    }

    @Test
    fun agentTurnStatesAreCompactPills() {
        for (kind in listOf(IslandKind.LISTENING, IslandKind.THINKING, IslandKind.TOOL, IslandKind.REPLYING, IslandKind.SPEAKING)) {
            val spec = islandSpec(IslandState(kind = kind), idle, expanded = false)
            assertEquals(kind.name, IslandSize.COMPACT, spec.size)
        }
    }

    @Test
    fun toolShowsSpinnerThenCheck() {
        val running = islandSpec(IslandState(kind = IslandKind.TOOL, title = "Searching the web"), idle, false)
        val done = islandSpec(IslandState(kind = IslandKind.TOOL, title = "Searching the web", toolDone = true), idle, false)
        assertEquals(IslandTrail.SPINNER, running.trail)
        assertEquals(IslandTrail.CHECK, done.trail)
        assertEquals("Searching the web", running.title)
    }

    @Test
    fun toolDetailListsTheToolsOfTheTurn() {
        val state = IslandState(kind = IslandKind.TOOL, title = "Reading memory", tools = listOf("Searching the web", "Reading memory"))
        val spec = islandSpec(state, idle, expanded = true)
        assertEquals(IslandSize.EXPANDED, spec.size)
        assertEquals("Tools this turn: Searching the web · Reading memory", spec.detail)
    }

    @Test
    fun aStateWithNothingMoreToSayDoesNotExpand() {
        val spec = islandSpec(IslandState(kind = IslandKind.THINKING), idle, expanded = true)
        assertEquals(IslandSize.COMPACT, spec.size)
    }

    @Test
    fun approvalIsAlwaysACardWithDenyAndApprove() {
        val spec = islandSpec(
            IslandState(kind = IslandKind.APPROVAL_REQUIRED, subtitle = "Send this message?", commandId = "c1"),
            idle,
            expanded = false,
        )
        assertEquals(IslandSize.EXPANDED, spec.size)
        assertEquals(listOf(IslandAction.DENY, IslandAction.APPROVE), spec.actions)
        assertEquals(IslandTone.WARN, spec.tone)
    }

    @Test
    fun phoneControlShowsTheStepAndStop() {
        val spec = islandSpec(
            IslandState(kind = IslandKind.WORKING, title = "Controlling Settings", subtitle = "Tapping Wi-Fi", step = 3),
            idle,
            expanded = false,
        )
        assertEquals(IslandSize.COMPACT, spec.size)
        assertEquals("Tapping Wi-Fi", spec.title)
        assertEquals(IslandTrail.STEP, spec.trail)
        assertTrue(IslandAction.STOP in spec.actions)
    }

    @Test
    fun aFinishedAgentTurnIsAQuickCompactDone() {
        val spec = islandSpec(IslandState(kind = IslandKind.COMPLETED, title = "Done", elapsedMs = 3_400, fromAgent = true), idle, false)
        assertEquals(IslandSize.COMPACT, spec.size)
        assertEquals(IslandTrail.ELAPSED, spec.trail)
        assertTrue(spec.dismissable)
    }

    @Test
    fun aFinishedPhoneTaskIsACard() {
        val spec = islandSpec(
            IslandState(kind = IslandKind.COMPLETED, title = "Task completed", subtitle = "Turned on dark theme.", commandId = "c1"),
            idle,
            expanded = false,
        )
        assertEquals(IslandSize.EXPANDED, spec.size)
        assertEquals(listOf(IslandAction.DISMISS), spec.actions)
    }

    @Test
    fun eachStateGetsItsExpression() {
        fun face(kind: IslandKind, fromAgent: Boolean = false, expanded: Boolean = false) =
            islandSpec(IslandState(kind = kind, fromAgent = fromAgent), idle, expanded).face

        assertEquals(IslandFace.SLEEPING, face(IslandKind.IDLE))
        assertEquals(IslandFace.NEUTRAL, face(IslandKind.IDLE, expanded = true))
        assertEquals(IslandFace.NEUTRAL, face(IslandKind.LISTENING))
        assertEquals(IslandFace.THINKING, face(IslandKind.THINKING))
        assertEquals(IslandFace.CODING, face(IslandKind.TOOL))
        assertEquals(IslandFace.TALKING, face(IslandKind.REPLYING))
        assertEquals(IslandFace.TALKING, face(IslandKind.SPEAKING))
        assertEquals(IslandFace.SCREENS, face(IslandKind.WORKING))
        assertEquals(IslandFace.IDEA, face(IslandKind.APPROVAL_REQUIRED))
        assertEquals(IslandFace.LAUGHING, face(IslandKind.COMPLETED, fromAgent = true))
        assertEquals(IslandFace.COOL, face(IslandKind.COMPLETED))
        assertEquals(IslandFace.THINKING, face(IslandKind.ERROR))
    }

    @Test
    fun durationsReadNaturally() {
        assertEquals("12s", formatDuration(12_400))
        assertEquals("4m", formatDuration(245_000))
        assertEquals("1h 05m", formatDuration(3_900_000))
        assertEquals("3.4s", formatTenths(3_460))
    }
}
