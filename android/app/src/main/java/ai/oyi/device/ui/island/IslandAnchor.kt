package ai.oyi.device.ui.island

import kotlin.math.max
import kotlin.math.roundToInt

/** The camera hole at the top of the screen, in pixels. */
data class CutoutRect(val left: Int, val top: Int, val right: Int, val bottom: Int) {
    val width: Int get() = right - left
    val height: Int get() = bottom - top
    val centerX: Int get() = (left + right) / 2
    val centerY: Int get() = (top + bottom) / 2
}

/**
 * Where the overlay window goes, and how big a hole the island keeps clear.
 *
 * [x] is the window's offset from the horizontal centre (its gravity is
 * TOP | CENTER_HORIZONTAL) and [y] its distance from the top, both in
 * pixels. [cutoutWidthDp] and [cutoutHeightDp] are zero when the island is
 * not sitting on a camera.
 */
data class IslandPlacement(
    val x: Int,
    val y: Int,
    val cutoutWidthDp: Float,
    val cutoutHeightDp: Float,
) {
    val onCamera: Boolean get() = cutoutWidthDp > 0f
}

/**
 * Centres the island on a punch-hole camera, the way a Dynamic Island grows
 * out of the hole.
 *
 * Only a small hole near the top counts: a wide notch, a corner camera far
 * off centre or no cutout at all leaves the island docked at the top centre.
 * [nudgeXDp] and [nudgeYDp] are the user's calibration on top of that;
 * [padTopDp] is the window's own padding above the pill.
 */
fun placeIsland(
    screenWidthPx: Int,
    density: Float,
    cutout: CutoutRect?,
    nudgeXDp: Int = 0,
    nudgeYDp: Int = 0,
    padTopDp: Int = 0,
    dockedTopDp: Int = 2,
): IslandPlacement {
    val nudgeX = (nudgeXDp * density).roundToInt()
    val nudgeY = (nudgeYDp * density).roundToInt()
    val usable = cutout != null &&
        cutout.width > 0 &&
        cutout.height > 0 &&
        cutout.width < screenWidthPx * MAX_HOLE_FRACTION &&
        cutout.top < 64 * density &&
        kotlin.math.abs(cutout.centerX - screenWidthPx / 2) < screenWidthPx * MAX_OFF_CENTRE_FRACTION
    if (!usable || cutout == null) {
        return IslandPlacement(x = nudgeX, y = (dockedTopDp * density).roundToInt() + nudgeY, 0f, 0f)
    }
    val holeW = cutout.width / density
    val holeH = cutout.height / density
    // The pill's height in cutout mode (see DynamicIsland's compactHeight).
    val pillH = max(40f, holeH + 12f) * density
    val pillTop = cutout.centerY - pillH / 2f
    return IslandPlacement(
        x = cutout.centerX - screenWidthPx / 2 + nudgeX,
        y = (pillTop - padTopDp * density).roundToInt().coerceAtLeast(0) + nudgeY,
        cutoutWidthDp = holeW,
        cutoutHeightDp = holeH,
    )
}

private const val MAX_HOLE_FRACTION = 0.3f
private const val MAX_OFF_CENTRE_FRACTION = 0.2f
