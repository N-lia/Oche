package ai.oyi.device.ui.theme

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CornerSize
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.asComposePath
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.drawscope.translate
import androidx.compose.ui.graphics.drawscope.scale
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.graphics.shapes.CornerRounding
import androidx.graphics.shapes.Morph
import androidx.graphics.shapes.RoundedPolygon
import androidx.graphics.shapes.circle
import androidx.graphics.shapes.star
import androidx.graphics.shapes.toPath

/**
 * The Material 3 Expressive shape set OYI uses, built with graphics-shapes.
 * Every polygon is centred on the origin with radius 1, so it can be scaled
 * to any size and morphed into any other.
 */
object OyiShapeSet {
    val Cookie: RoundedPolygon = RoundedPolygon.star(
        numVerticesPerRadius = 9,
        innerRadius = 0.82f,
        rounding = CornerRounding(0.22f, smoothing = 0.6f),
        innerRounding = CornerRounding(0.22f, smoothing = 0.6f),
    )
    val Sunny: RoundedPolygon = RoundedPolygon.star(
        numVerticesPerRadius = 8,
        innerRadius = 0.78f,
        rounding = CornerRounding(0.12f, smoothing = 0.4f),
    )
    val Clover: RoundedPolygon = RoundedPolygon.star(
        numVerticesPerRadius = 4,
        innerRadius = 0.48f,
        rounding = CornerRounding(0.42f, smoothing = 0.9f),
        innerRounding = CornerRounding(0.1f),
    )
    val Pentagon: RoundedPolygon = RoundedPolygon(
        numVertices = 5,
        rounding = CornerRounding(0.32f, smoothing = 0.8f),
    )
    val Gem: RoundedPolygon = RoundedPolygon(
        numVertices = 6,
        rounding = CornerRounding(0.28f, smoothing = 0.7f),
    )
    val Soft: RoundedPolygon = RoundedPolygon.circle(numVertices = 10)

    /** The loading cycle, in the order the shapes morph. */
    val LoadingCycle: List<RoundedPolygon> = listOf(Cookie, Pentagon, Clover, Sunny, Gem, Soft)
}

/**
 * An expressive loading indicator: a blob that morphs through the shape set
 * while it turns. Replaces the thin spinner wherever OYI is busy.
 */
@Composable
fun MorphingLoader(
    color: Color,
    modifier: Modifier = Modifier,
    size: Dp = 24.dp,
    shapes: List<RoundedPolygon> = OyiShapeSet.LoadingCycle,
    periodMs: Int = 650,
) {
    val morphs = remember(shapes) {
        shapes.indices.map { i -> Morph(shapes[i], shapes[(i + 1) % shapes.size]) }
    }
    val still = LocalInspectionMode.current
    val clock = if (still) {
        0.35f
    } else {
        val transition = rememberInfiniteTransition(label = "loader")
        val value by transition.animateFloat(
            initialValue = 0f,
            targetValue = morphs.size.toFloat(),
            animationSpec = infiniteRepeatable(tween(periodMs * morphs.size, easing = LinearEasing)),
            label = "loaderClock",
        )
        value
    }
    val index = clock.toInt().coerceIn(0, morphs.size - 1)
    val progress = snappy(clock - index)
    val path = remember { android.graphics.Path() }
    Canvas(modifier.size(size)) {
        val radius = this.size.minDimension / 2f
        morphs[index].toPath(progress, path)
        val composePath = path.asComposePath()
        translate(center.x, center.y) {
            rotate(degrees = clock * 140f, pivot = Offset.Zero) {
                scale(radius * 0.92f, radius * 0.92f, pivot = Offset.Zero) {
                    drawPath(composePath, color)
                }
            }
        }
    }
}

/** Holds each shape, then snaps to the next with a little overshoot. */
private fun snappy(t: Float): Float {
    val hold = 0.35f
    if (t <= hold) return 0f
    val x = (t - hold) / (1f - hold)
    // easeOutBack: overshoots slightly, settles at 1.
    val c1 = 1.4f
    val c3 = c1 + 1f
    val p = x - 1f
    return (1f + c3 * p * p * p + c1 * p * p).coerceIn(0f, 1.08f)
}

/**
 * An expressive button surface: it squishes and its corners tighten while
 * pressed, then springs back. [content] gets the live shape, so a background
 * or glass can follow it.
 */
@Composable
fun PressMorph(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    restPercent: Float = 50f,
    pressedPercent: Float = 24f,
    content: @Composable BoxScope.(shape: Shape) -> Unit,
) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val corner by animateFloatAsState(
        targetValue = if (pressed) pressedPercent else restPercent,
        animationSpec = OyiMotion.fastSpatial(),
        label = "pressCorner",
    )
    val squish by animateFloatAsState(
        targetValue = if (pressed) 0.94f else 1f,
        animationSpec = OyiMotion.fastSpatial(),
        label = "pressScale",
    )
    val shape = RoundedCornerShape(CornerSize(corner.coerceIn(0f, 50f)))
    Box(
        modifier = modifier
            .graphicsLayer {
                scaleX = squish
                scaleY = squish
            }
            .clip(shape)
            .clickable(
                interactionSource = interaction,
                indication = ripple(),
                enabled = enabled,
                onClick = onClick,
            ),
        contentAlignment = Alignment.Center,
    ) {
        content(shape)
    }
}
