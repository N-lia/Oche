package ai.oyi.device.ui.theme

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.drawOutline
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.chrisbanes.haze.HazeState
import dev.chrisbanes.haze.HazeStyle
import dev.chrisbanes.haze.HazeTint
import dev.chrisbanes.haze.haze
import dev.chrisbanes.haze.hazeChild
import kotlin.math.cos
import kotlin.math.sin

/**
 * The backdrop that glass surfaces blur. Null where there is nothing behind
 * the glass to blur (the overlay over other apps, previews): the glass then
 * falls back to a tinted fill with the same lighting.
 */
val LocalGlassBackdrop = staticCompositionLocalOf<HazeState?> { null }

/**
 * A screen with a slowly drifting ambient glow behind it. Glass cards on the
 * screen blur and tint that glow, which is where the liquid glass reads.
 */
@Composable
fun GlassBackdrop(
    modifier: Modifier = Modifier,
    accent: Color = LocalOyiAccent.current,
    content: @Composable BoxScope.() -> Unit,
) {
    val state = remember { HazeState() }
    Box(modifier.fillMaxSize().background(Color(0xFF070708))) {
        AmbientGlow(accent, Modifier.fillMaxSize().haze(state))
        CompositionLocalProvider(LocalGlassBackdrop provides state) {
            content()
        }
    }
}

/** Three soft colour fields that drift on a long loop. */
@Composable
private fun AmbientGlow(accent: Color, modifier: Modifier) {
    val still = LocalInspectionMode.current
    val drift = if (still) {
        0f
    } else {
        val transition = rememberInfiniteTransition(label = "glow")
        val value by transition.animateFloat(
            initialValue = 0f,
            targetValue = (2 * Math.PI).toFloat(),
            animationSpec = infiniteRepeatable(tween(26_000, easing = LinearEasing), RepeatMode.Restart),
            label = "drift",
        )
        value
    }
    Canvas(modifier) {
        val w = size.width
        val h = size.height
        fun glow(color: Color, cx: Float, cy: Float, r: Float) {
            drawCircle(
                brush = Brush.radialGradient(listOf(color, Color.Transparent), center = Offset(cx, cy), radius = r),
                radius = r,
                center = Offset(cx, cy),
            )
        }
        glow(accent.copy(alpha = 0.34f), w * (0.18f + 0.08f * cos(drift)), h * (0.20f + 0.05f * sin(drift)), w * 0.75f)
        glow(Color(0x40F7E27E), w * (0.92f + 0.06f * sin(drift * 2)), h * (0.48f + 0.06f * cos(drift)), w * 0.62f)
        glow(Color(0x30F7EDD0), w * (0.30f + 0.10f * sin(drift)), h * (0.92f + 0.04f * cos(drift * 2)), w * 0.70f)
    }
}

/** Top-lit rim: bright where the light hits, nearly gone at the bottom. */
val GlassRimLight = Brush.verticalGradient(listOf(Color(0x8CFFFFFF), Color(0x1AFFFFFF), Color(0x0DFFFFFF), Color(0x33FFFFFF)))

/** The soft sheen across the top half of a glass surface. */
private val GlassSheen = Brush.verticalGradient(
    0f to Color(0x24FFFFFF),
    0.42f to Color(0x05FFFFFF),
    1f to Color.Transparent,
)

/**
 * Liquid glass: a blurred, tinted view of the backdrop with a specular sheen
 * on top and a light-catching rim. Subtle by design: it should read as a
 * material, not a special effect.
 *
 * [fill] is drawn over the blur, so give it some transparency. Without a
 * [LocalGlassBackdrop] (or below Android 12) the blur becomes a plain tint.
 */
fun Modifier.liquidGlass(
    shape: Shape,
    fill: Brush,
    backdrop: HazeState?,
    elevation: Dp = 12.dp,
    tint: Color = Color(0x660E0E12),
    blur: Dp = 28.dp,
    rim: Brush = GlassRimLight,
    sheen: Boolean = true,
): Modifier {
    val base = this
        .shadow(elevation, shape, clip = false, ambientColor = Color.Black, spotColor = Color.Black)
        .clip(shape)
    val blurred = if (backdrop != null) {
        base.hazeChild(
            state = backdrop,
            style = HazeStyle(
                backgroundColor = Color(0xFF070708),
                tint = HazeTint(tint),
                blurRadius = blur,
                noiseFactor = 0.05f,
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
            // The rim is drawn twice as wide because the clip hides its outer half.
            drawOutline(outline, rim, style = Stroke(width = 2.dp.toPx()))
        }
}
