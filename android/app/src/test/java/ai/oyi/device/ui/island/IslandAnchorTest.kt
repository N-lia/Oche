package ai.oyi.device.ui.island

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class IslandAnchorTest {

    private val width = 1080
    private val density = 2.75f

    @Test
    fun centresOnAPunchHole() {
        // A 50 px hole near the top, a little left of the middle.
        val hole = CutoutRect(left = 510, top = 60, right = 560, bottom = 110)
        val placement = placeIsland(width, density, hole)
        assertTrue(placement.onCamera)
        assertEquals(535 - 540, placement.x)
        // Pill 40 dp (110 px) high, centred on the hole's centre at y = 85.
        assertEquals(30, placement.y)
    }

    @Test
    fun noCutoutDocksAtTheTop() {
        val placement = placeIsland(width, density, cutout = null)
        assertFalse(placement.onCamera)
        assertEquals(0, placement.x)
        assertEquals((2 * density).toInt(), placement.y)
    }

    @Test
    fun aWideNotchIsNotAHole() {
        val notch = CutoutRect(left = 300, top = 0, right = 780, bottom = 90)
        assertFalse(placeIsland(width, density, notch).onCamera)
    }

    @Test
    fun aCornerCameraIsIgnored() {
        val corner = CutoutRect(left = 60, top = 30, right = 110, bottom = 80)
        assertFalse(placeIsland(width, density, corner).onCamera)
    }

    @Test
    fun calibrationNudgesTheWindow() {
        val hole = CutoutRect(left = 515, top = 30, right = 565, bottom = 80)
        val base = placeIsland(width, density, hole)
        val nudged = placeIsland(width, density, hole, nudgeXDp = 4, nudgeYDp = -2)
        assertEquals(base.x + 11, nudged.x)
        assertEquals(base.y - 5, nudged.y)
    }
}
