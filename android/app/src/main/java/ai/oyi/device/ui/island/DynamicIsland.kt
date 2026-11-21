package ai.oyi.device.ui.island

import ai.oyi.device.R
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.sin

/** Colours of the island: always black glass, one accent per state. */
internal object IslandColors {
    val Shell = Color(0xFF050506)
    val Rim = Brush.verticalGradient(listOf(Color(0x40FFFFFF), Color(0x0DFFFFFF)))
    val Ink = Color(0xFFF8F6F1)
    val Dim = Color(0xFF9C978E)
    val Cream = Color(0xFFF7EDD0)
    val InkOnLight = Color(0xFF0A0807)

    fun tone(tone: IslandTone): Color = when (tone) {
        IslandTone.NEUTRAL -> Color(0xFF8A867E)
        IslandTone.ACCENT -> Color(0xFFF08A65)
        IslandTone.LIVE -> Color(0xFF5FD38D)
        IslandTone.OK -> Color(0xFF5FD38D)
        IslandTone.WARN -> Color(0xFFF5B547)
        IslandTone.BAD -> Color(0xFFFF6B6B)
    }
}

private val IslandSpring = spring<Float>(dampingRatio = 0.72f, stiffness = Spring.StiffnessMediumLow)

/**
 * The OYI Dynamic Island.
 *
 * One composable for both places it appears: the overlay on top of other
 * apps and the header inside OYI. It morphs between a resting capsule, a
 * compact status pill and an expanded card (see [islandSpec]).
 *
 * Gestures: tap opens or closes the detail (or closes a result), swipe up
 * folds it away, long press opens OYI.
 */
@Composable
fun DynamicIsland(
    state: IslandState,
    idle: IslandIdleInfo,
    level: Float,
    onAction: (IslandAction) -> Unit,
    modifier: Modifier = Modifier,
    onLongPress: () -> Unit = {},
    /** Inside OYI itself: no "Open OYI" button. */
    inApp: Boolean = false,
    initiallyExpanded: Boolean = false,
) {
    var expanded by remember { mutableStateOf(initiallyExpanded) }
    val haptics = LocalHapticFeedback.current

    // A new state starts folded; a card that needs the user buzzes once.
    LaunchedEffect(state.kind, state.commandId) {
        if (!initiallyExpanded) expanded = false
        if (state.kind == IslandKind.APPROVAL_REQUIRED || state.kind == IslandKind.ERROR) {
            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
        }
    }
    // The idle drawer folds itself after a while.
    LaunchedEffect(expanded, state.kind) {
        if (expanded && state.kind == IslandKind.IDLE && !initiallyExpanded) {
            delay(6_000)
            expanded = false
        }
    }

    val spec = islandSpec(state, idle, expanded).let { spec ->
        if (inApp) spec.copy(actions = spec.actions - IslandAction.OPEN_APP) else spec
    }
    val tone = IslandColors.tone(spec.tone)
    val corner by animateDpAsState(
        targetValue = if (spec.size == IslandSize.EXPANDED) 30.dp else 22.dp,
        animationSpec = spring(dampingRatio = 0.8f, stiffness = Spring.StiffnessMediumLow),
        label = "corner",
    )
    val shape = RoundedCornerShape(corner)

    Box(
        modifier = modifier
            .semantics {
                liveRegion = LiveRegionMode.Polite
                contentDescription = listOf(spec.title, spec.detail).filter { it.isNotBlank() }.joinToString(". ")
            }
            .shadow(if (spec.size == IslandSize.REST) 8.dp else 18.dp, shape, clip = false)
            .clip(shape)
            .background(IslandColors.Shell)
            .border(1.dp, IslandColors.Rim, shape)
            .pointerInput(spec.expandable, spec.dismissable) {
                detectTapGestures(
                    onTap = {
                        when {
                            spec.expandable -> expanded = !expanded
                            spec.dismissable -> onAction(IslandAction.DISMISS)
                        }
                    },
                    onLongPress = {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        onLongPress()
                    },
                )
            }
            .pointerInput(spec.dismissable) {
                var dragged = 0f
                detectVerticalDragGestures(
                    onDragStart = { dragged = 0f },
                    onVerticalDrag = { change, amount ->
                        dragged += amount
                        change.consume()
                    },
                    onDragEnd = {
                        if (dragged < -36f) {
                            if (expanded) expanded = false
                            else if (spec.dismissable) onAction(IslandAction.DISMISS)
                        } else if (dragged > 36f && spec.expandable) {
                            expanded = true
                        }
                    },
                )
            },
    ) {
        AnimatedContent(
            targetState = spec,
            contentKey = { Triple(it.size, it.lead, it.actions) },
            transitionSpec = {
                (fadeIn(tween(220, delayMillis = 110)) + scaleIn(tween(260, delayMillis = 80), initialScale = 0.92f))
                    .togetherWith(fadeOut(tween(90)))
                    .using(
                        SizeTransform(clip = true) { _, _ ->
                            spring(dampingRatio = 0.74f, stiffness = Spring.StiffnessMediumLow)
                        }
                    )
            },
            label = "island",
        ) { target ->
            when (target.size) {
                IslandSize.REST -> RestPill(target, idle, tone)
                IslandSize.COMPACT -> CompactPill(target, state, level, tone)
                IslandSize.EXPANDED -> ExpandedCard(target, state, level, tone, onAction)
            }
        }
    }
}

// ------------------------------------------------------------------ sizes

@Composable
private fun RestPill(spec: IslandSpec, idle: IslandIdleInfo, tone: Color) {
    Row(
        modifier = Modifier
            .height(34.dp)
            .widthIn(min = 112.dp, max = 236.dp)
            .padding(horizontal = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LinkDot(tone, pulsing = spec.tone == IslandTone.WARN)
        Spacer(Modifier.width(8.dp))
        Text(
            text = spec.title,
            color = IslandColors.Ink,
            fontSize = 12.5.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f, fill = false),
        )
        if (spec.trail == IslandTrail.CLOCK) {
            Spacer(Modifier.width(8.dp))
            Text(
                text = formatDuration(idle.sessionMs),
                color = IslandColors.Cream,
                fontSize = 11.5.sp,
                fontFamily = FontFamily.Monospace,
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun CompactPill(spec: IslandSpec, state: IslandState, level: Float, tone: Color) {
    Row(
        modifier = Modifier
            .height(40.dp)
            .widthIn(max = 300.dp)
            .fillMaxWidth()
            .padding(start = 8.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(28.dp), contentAlignment = Alignment.Center) {
            Lead(spec.lead, tone, iconSize = 18.dp)
        }
        Spacer(Modifier.width(10.dp))
        Text(
            text = spec.title,
            color = IslandColors.Ink,
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(10.dp))
        Trail(spec, state, level, tone)
    }
}

@Composable
private fun ExpandedCard(
    spec: IslandSpec,
    state: IslandState,
    level: Float,
    tone: Color,
    onAction: (IslandAction) -> Unit,
) {
    Column(
        modifier = Modifier
            .widthIn(max = 352.dp)
            .fillMaxWidth()
            .padding(start = 14.dp, end = 16.dp, top = 14.dp, bottom = 14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .clip(CircleShape)
                    .background(tone.copy(alpha = 0.18f)),
                contentAlignment = Alignment.Center,
            ) {
                Lead(spec.lead, tone, iconSize = 22.dp)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                if (spec.badge.isNotBlank()) {
                    Text(
                        text = spec.badge,
                        color = tone,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 0.9.sp,
                        maxLines = 1,
                    )
                    Spacer(Modifier.height(2.dp))
                }
                Text(
                    text = spec.title,
                    color = IslandColors.Ink,
                    fontSize = 15.5.sp,
                    fontWeight = FontWeight.Bold,
                    lineHeight = 19.sp,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (spec.trail != IslandTrail.NONE && spec.trail != IslandTrail.CLOCK) {
                Spacer(Modifier.width(10.dp))
                Trail(spec, state, level, tone)
            }
        }
        if (spec.detail.isNotBlank()) {
            Text(
                text = spec.detail,
                color = IslandColors.Dim,
                fontSize = 13.sp,
                lineHeight = 18.sp,
                maxLines = 3,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(start = 56.dp, top = 6.dp),
            )
        }
        if (spec.actions.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                spec.actions.forEach { action -> ActionPill(action, tone, onAction) }
            }
        }
    }
}

// ------------------------------------------------------------- pieces

@Composable
private fun Lead(lead: IslandLead, tone: Color, iconSize: Dp) {
    when (lead) {
        IslandLead.DOT -> LinkDot(tone, pulsing = false, size = 10.dp)
        IslandLead.SPINNER -> Spinner(tone, size = iconSize - 2.dp)
        IslandLead.ORB -> Orb(size = iconSize)
        else -> Icon(
            painter = painterResource(iconFor(lead)),
            contentDescription = null,
            tint = tone,
            modifier = Modifier.size(iconSize),
        )
    }
}

@DrawableRes
private fun iconFor(lead: IslandLead): Int = when (lead) {
    IslandLead.MIC -> R.drawable.ic_island_mic
    IslandLead.TOOL -> R.drawable.ic_island_tool
    IslandLead.PEN -> R.drawable.ic_island_pen
    IslandLead.PHONE -> R.drawable.ic_island_phone
    IslandLead.CHECK -> R.drawable.ic_island_check
    IslandLead.ALERT -> R.drawable.ic_island_alert
    IslandLead.ASK -> R.drawable.ic_island_ask
    else -> R.drawable.ic_island_check
}

@Composable
private fun Trail(spec: IslandSpec, state: IslandState, level: Float, tone: Color) {
    when (spec.trail) {
        IslandTrail.TIMER -> LiveTimer(state.startedAt)
        IslandTrail.ELAPSED -> MonoText(if (state.elapsedMs > 0) formatTenths(state.elapsedMs) else "")
        IslandTrail.BARS -> SoundBars(
            level = level,
            tone = tone,
            synthetic = state.kind == IslandKind.SPEAKING,
        )
        IslandTrail.SPINNER -> Spinner(tone, size = 15.dp)
        IslandTrail.CHECK -> Icon(
            painter = painterResource(R.drawable.ic_island_check),
            contentDescription = null,
            tint = tone,
            modifier = Modifier.size(16.dp),
        )
        IslandTrail.STEP -> Box(
            modifier = Modifier
                .clip(RoundedCornerShape(999.dp))
                .background(tone.copy(alpha = 0.18f))
                .padding(horizontal = 8.dp, vertical = 3.dp),
        ) {
            Text(
                text = "Step ${state.step}",
                color = tone,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
            )
        }
        IslandTrail.CLOCK, IslandTrail.NONE -> Unit
    }
}

@Composable
private fun MonoText(text: String) {
    Text(
        text = text,
        color = IslandColors.Dim,
        fontSize = 12.sp,
        fontFamily = FontFamily.Monospace,
        maxLines = 1,
    )
}

@Composable
private fun LiveTimer(startedAt: Long) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(startedAt) {
        while (true) {
            now = System.currentTimeMillis()
            delay(100)
        }
    }
    MonoText(if (startedAt > 0) formatTenths(now - startedAt) else "")
}

@Composable
private fun LinkDot(tone: Color, pulsing: Boolean, size: Dp = 8.dp) {
    val alpha = if (pulsing) {
        val transition = rememberInfiniteTransition(label = "dot")
        val value by transition.animateFloat(
            initialValue = 0.35f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(650, easing = FastOutSlowInEasing), RepeatMode.Reverse),
            label = "dotAlpha",
        )
        value
    } else {
        1f
    }
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(tone.copy(alpha = alpha)),
    )
}

@Composable
private fun Spinner(tone: Color, size: Dp) {
    CircularProgressIndicator(
        modifier = Modifier.size(size),
        color = tone,
        strokeWidth = 2.dp,
        trackColor = tone.copy(alpha = 0.22f),
        strokeCap = StrokeCap.Round,
    )
}

/** OYI's voice: a warm orb that breathes while it talks. */
@Composable
private fun Orb(size: Dp) {
    val transition = rememberInfiniteTransition(label = "orb")
    val scale by transition.animateFloat(
        initialValue = 0.86f,
        targetValue = 1.06f,
        animationSpec = infiniteRepeatable(tween(800, easing = FastOutSlowInEasing), RepeatMode.Reverse),
        label = "orbScale",
    )
    Box(
        Modifier
            .size(size)
            .scale(scale)
            .clip(CircleShape)
            .background(
                Brush.radialGradient(
                    listOf(Color(0xFFFFD2BF), Color(0xFFF08A65), Color(0xFFB4492A)),
                ),
            ),
    )
}

private val BarGains = floatArrayOf(0.55f, 0.85f, 1f, 0.8f, 0.6f)

/**
 * Five bars. While listening they follow the microphone level; while OYI
 * speaks they follow a gentle synthetic wave (playback has no meter here).
 */
@Composable
private fun SoundBars(level: Float, tone: Color, synthetic: Boolean) {
    val smoothed by animateFloatAsState(level, animationSpec = IslandSpring, label = "level")
    val transition = rememberInfiniteTransition(label = "bars")
    val phase by transition.animateFloat(
        initialValue = 0f,
        targetValue = (2 * PI).toFloat(),
        animationSpec = infiniteRepeatable(tween(1_100, easing = LinearEasing)),
        label = "phase",
    )
    Canvas(Modifier.size(width = 27.dp, height = 18.dp)) {
        val barWidth = 3.dp.toPx()
        val gap = (size.width - barWidth * BarGains.size) / (BarGains.size - 1)
        val base = if (synthetic) 0.45f + 0.25f * sin(phase * 2) else smoothed
        BarGains.forEachIndexed { i, gain ->
            val wobble = if (base > 0.04f) 0.14f * sin(phase * 3 + i * 1.7f) else 0f
            val h = (base * gain * 1.5f + wobble).coerceIn(0.18f, 1f) * size.height
            drawRoundRect(
                color = tone,
                topLeft = Offset(i * (barWidth + gap), (size.height - h) / 2f),
                size = Size(barWidth, h),
                cornerRadius = CornerRadius(barWidth / 2f, barWidth / 2f),
            )
        }
    }
}

@Composable
private fun ActionPill(action: IslandAction, tone: Color, onAction: (IslandAction) -> Unit) {
    val (label, filled) = when (action) {
        IslandAction.APPROVE -> "Approve" to true
        IslandAction.DENY -> "Deny" to false
        IslandAction.STOP -> "Stop" to true
        IslandAction.DISMISS -> "Close" to false
        IslandAction.OPEN_APP -> "Open OYI" to true
    }
    val background = when {
        !filled -> Color(0x1FFFFFFF)
        action == IslandAction.STOP -> Color(0xFFFF6B6B)
        action == IslandAction.OPEN_APP -> IslandColors.Cream
        else -> tone
    }
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(999.dp))
            .background(background)
            .clickable { onAction(action) }
            .padding(horizontal = 16.dp, vertical = 8.dp),
    ) {
        Text(
            text = label,
            color = if (filled) IslandColors.InkOnLight else IslandColors.Ink,
            fontSize = 13.sp,
            fontWeight = FontWeight.Bold,
        )
    }
}
