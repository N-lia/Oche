package ai.oyi.device.ui

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.drawable.toBitmap

/** The OYI look: pitch black, warm glass, one orange accent. */
internal object Oyi {
    val Black = Color(0xFF070708)
    val OffWhite = Color(0xFFF8F6F1)
    val Muted = Color(0xFFA09C94)
    val Faint = Color(0xFF6B6862)
    val Orange = Color(0xFFF08A65)
    val OrangeTop = Color(0xFFFFA584)
    val OrangeBottom = Color(0xFFE07450)
    val Cream = Color(0xFFF7EDD0)
    val Yellow = Color(0xFFF7E27E)
    val Ink = Color(0xFF0A0807)
    val InkSoft = Color(0xFF3D3624)
    val Hairline = Color(0x1FFFFFFF)

    val Pill = RoundedCornerShape(999.dp)
    val Card = RoundedCornerShape(28.dp)

    val DarkRim = Brush.verticalGradient(listOf(Color(0x59FFFFFF), Color(0x14FFFFFF)))
    val WarmRim = Brush.verticalGradient(listOf(Color(0xB3FFF9F2), Color(0x2EFFFFFF)))
    val SmokedGlass = Brush.linearGradient(listOf(Color(0xD926262D), Color(0xCC141418)))
    val OrangeGlass = Brush.linearGradient(listOf(OrangeTop, Orange, OrangeBottom))
    val CreamGlass = Brush.linearGradient(listOf(Color(0xFFFFF8E6), Cream))
    val RingSweep = Brush.sweepGradient(listOf(OrangeTop, Yellow, Orange, OrangeTop))
}

@Composable
internal fun GlassCard(
    modifier: Modifier = Modifier,
    brush: Brush = Oyi.SmokedGlass,
    rim: Brush = Oyi.DarkRim,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .shadow(12.dp, Oyi.Card)
            .clip(Oyi.Card)
            .background(brush)
            .border(1.dp, rim, Oyi.Card)
            .padding(20.dp),
        content = content,
    )
}

/** A small rounded choice. Dimmed (but still tappable) when [enabled] is false. */
@Composable
internal fun Pill(
    text: String,
    selected: Boolean = false,
    enabled: Boolean = true,
    onDark: Boolean = true,
    onClick: () -> Unit,
) {
    val background = when {
        selected && onDark -> Oyi.Cream
        selected -> Oyi.Ink
        onDark -> Color(0x14FFFFFF)
        else -> Color(0x1F0A0807)
    }
    val foreground = when {
        selected && onDark -> Oyi.Ink
        selected -> Oyi.Cream
        onDark -> Oyi.OffWhite
        else -> Oyi.Ink
    }
    Box(
        modifier = Modifier
            .alpha(if (enabled) 1f else 0.35f)
            .clip(Oyi.Pill)
            .background(background)
            .border(1.dp, if (onDark) Oyi.Hairline else Color(0x33FFFFFF), Oyi.Pill)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = foreground, fontSize = 12.5.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
    }
}

/** Tiny spaced-out caption used above sections. */
@Composable
internal fun Eyebrow(text: String, color: Color = Oyi.Muted) {
    Text(
        text = text.uppercase(),
        color = color,
        fontSize = 10.5.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.6.sp,
    )
}

/** The progress ring of a focus block. [progress] runs from 0 to 1. */
@Composable
internal fun FocusRing(
    progress: Float,
    modifier: Modifier = Modifier,
    strokeWidth: Dp = 10.dp,
    content: @Composable () -> Unit = {},
) {
    val animated by animateFloatAsState(
        targetValue = progress.coerceIn(0f, 1f),
        animationSpec = tween(durationMillis = 900),
        label = "focus-ring",
    )
    Box(modifier = modifier, contentAlignment = Alignment.Center) {
        Canvas(modifier = Modifier.matchParentSize()) {
            val stroke = strokeWidth.toPx()
            val inset = stroke / 2f
            val arcSize = Size(size.width - stroke, size.height - stroke)
            val topLeft = Offset(inset, inset)
            drawArc(
                color = Color(0x14FFFFFF),
                startAngle = 0f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = topLeft,
                size = arcSize,
                style = Stroke(width = stroke),
            )
            drawArc(
                brush = Oyi.RingSweep,
                startAngle = -90f,
                sweepAngle = 360f * animated,
                useCenter = false,
                topLeft = topLeft,
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )
        }
        content()
    }
}

/** An installed app's own icon, with a quiet placeholder when it has none. */
@Composable
internal fun AppIcon(packageName: String, size: Dp = 38.dp) {
    val context = LocalContext.current
    val bitmap = remember(packageName) {
        runCatching {
            context.packageManager.getApplicationIcon(packageName).toBitmap(96, 96).asImageBitmap()
        }.getOrNull()
    }
    val shape = RoundedCornerShape(12.dp)
    if (bitmap != null) {
        Image(
            bitmap = bitmap,
            contentDescription = null,
            modifier = Modifier.size(size).clip(shape),
        )
    } else {
        Box(
            modifier = Modifier.size(size).clip(shape).background(Color(0x1FFFFFFF)),
        )
    }
}

internal fun formatClock(millis: Long): String {
    val totalSeconds = (millis.coerceAtLeast(0L) + 999L) / 1000L
    val hours = totalSeconds / 3600L
    val minutes = (totalSeconds % 3600L) / 60L
    val seconds = totalSeconds % 60L
    return if (hours > 0) {
        "%d:%02d:%02d".format(hours, minutes, seconds)
    } else {
        "%02d:%02d".format(minutes, seconds)
    }
}

/** "17 days", "9h 12m", "42m": a span a person would say out loud. */
internal fun formatSpan(millis: Long): String {
    val minutes = (millis.coerceAtLeast(0L) + 59_999L) / 60_000L
    val days = minutes / 1440L
    val hours = (minutes % 1440L) / 60L
    val rest = minutes % 60L
    return when {
        days >= 2 -> "$days days"
        days == 1L -> if (hours > 0) "1 day ${hours}h" else "1 day"
        hours > 0 -> if (rest > 0) "${hours}h ${rest}m" else "${hours}h"
        minutes > 0 -> "${minutes}m"
        else -> "under a minute"
    }
}
