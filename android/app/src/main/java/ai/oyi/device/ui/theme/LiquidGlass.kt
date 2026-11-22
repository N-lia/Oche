package ai.oyi.device.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawOutline
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.HazeStyle
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.haze
import dev.chrisbanes.haze.hazeChild

/**
 * What glass surfaces blur. Null where nothing scrolls behind the glass (the
 * overlay over other apps, previews): the glass then falls back to its fill.
 */
val LocalGlassBackdrop = staticCompositionLocalOf<HazeState?> { null }

/**
 * A true black screen. Black matches the island and the camera, and it
 * makes the few pieces of glass (the floating nav, the composer) read as
 * glass because real content scrolls under them, not a decorative glow.
 *
 * Mark the scrolling content with [glassSource]; glass siblings drawn on top
 * of it then blur it.
 */
@Composable
fun GlassBackdrop(
    modifier: Modifier = Modifier,
    content: @Composable BoxScope.() -> Unit,
) {
    val state = remember { HazeState() }
    CompositionLocalProvider(LocalGlassBackdrop provides state) {
        Box(modifier.fillMaxSize().background(Color.Black), content = content)
    }
}

/** Marks content as the thing glass surfaces blur. */
fun Modifier.glassSource(): Modifier = composed {
    val state = LocalGlassBackdrop.current
    if (state == null) this else this.haze(state)
}

/** A single light hairline: a touch brighter along the top edge. */
val GlassRimLight = Brush.verticalGradient(listOf(Color(0x38FFFFFF), Color(0x14FFFFFF), Color(0x0FFFFFFF)))

/** A barely-there sheen on the top third, so the glass has a top. */
private val GlassSheen = Brush.verticalGradient(
    0f to Color(0x12FFFFFF),
    0.35f to Color(0x00FFFFFF),
    1f to Color.Transparent,
)

/**
 * Liquid glass: a blurred, darkened view of what is behind, with a hairline
 * rim. Subtle by design: it should read as a material, not an effect.
 *
 * [fill] is drawn over the blur, so give it some transparency. Without a
 * backdrop (or below Android 12) the blur becomes the plain fill.
 */
fun Modifier.liquidGlass(
    shape: Shape,
    fill: Brush = SolidColor(Color(0xB8141416)),
    backdrop: HazeState?,
    elevation: Dp = 0.dp,
    tint: Color = Color(0x99101012),
    blur: Dp = 24.dp,
    rim: Brush = GlassRimLight,
    sheen: Boolean = false,
): Modifier {
    val base = (if (elevation > 0.dp) this.shadow(elevation, shape, clip = false, ambientColor = Color.Black, spotColor = Color.Black) else this)
        .clip(shape)
    val blurred = if (backdrop != null) {
        base.hazeChild(
            state = backdrop,
            style = HazeStyle(
                backgroundColor = Color.Black,
                tint = HazeTint(tint),
                blurRadius = blur,
                noiseFactor = 0.03f,
            ),
        )
    } else {
        base
    }
    return blurred
        .background(fill)
        .drawWithContent {
            drawContent()
            val outline = shape.createOutline(size, layoutDirection, this)
            if (sheen) drawOutline(outline, GlassSheen)
            // Drawn 2px wide because the clip hides the outer half: 1px shows.
            drawOutline(outline, rim, style = Stroke(width = 2.dp.toPx()))
        }
}
