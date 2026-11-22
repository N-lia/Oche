package ai.oyi.device.ui.island

import ai.oyi.device.R
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import ai.oyi.device.ui.theme.MorphingLoader
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.PressMorph
import ai.oyi.device.ui.theme.TabularNumbers
import ai.oyi.device.ui.theme.liquidGlass
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.SizeTransform
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.keyframes
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandHorizontally
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.animation.shrinkHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
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
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalInspectionMode
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics

import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.sin

/** Colours of the island: black glass that hides the camera, one accent per state. */
internal object IslandColors {
    val Shell = Color(0xFF000000)
    val Ink = Color(0xFFF8F6F1)
    val Dim = Color(0xFF9C978E)
    val Cream = Color(0xFFF7EDD0)
    val InkOnLight = Color(0xFF0A0807)
    val BrandAccent = Color(0xFFF08A65)

    /** A thin, top-lit rim: the glass edge catching light above the camera. */
    val Rim = Brush.verticalGradient(listOf(Color(0x2EFFFFFF), Color(0x14FFFFFF), Color(0x14FFFFFF)))

    fun tone(tone: IslandTone, accent: Color = BrandAccent): Color = when (tone) {
        IslandTone.NEUTRAL -> Color(0xFF8A867E)
        IslandTone.ACCENT -> accent
        IslandTone.LIVE -> Color(0xFF5FD38D)
        IslandTone.OK -> Color(0xFF5FD38D)
        IslandTone.WARN -> Color(0xFFF5B547)
        IslandTone.BAD -> Color(0xFFFF6B6B)
    }
}

/**
 * The OYI Dynamic Island.
 *
 * One composable for both places it appears: the overlay on top of other
 * apps and the header inside OYI. It morphs between a resting capsule, a
 * compact status pill and an expanded card (see [islandSpec]).
 *
 * Motion is split in two, Material 3 Expressive style: the shell resizes on
 * a spatial spring first, then the content fades and scales in on an
 * effects spring. Each state moves the face its own way ([IslandMotion]),
 * and a light runs around the rim while work is under way.
 *
 * Gestures: tap opens or closes the detail (or closes a result), double tap
 * interrupts a spoken reply or stops phone control, swipe up folds it away,
 * swipe sideways (or tap the second bubble) swaps to the other activity,
 * long press opens OYI.
 *
 * [secondary] is an activity running behind the one shown (for example the
 * agent talking while phone control runs). It sits in a small bubble beside
 * the island. [cutout] is the size of the camera hole the island is centred
 * on, so content can keep clear of it; zero inside the app.
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
    /** Show Caleb's expression stickers instead of plain icons. */
    faces: Boolean = true,
    initiallyExpanded: Boolean = false,
    secondary: IslandState? = null,
    cutout: DpSize = DpSize.Zero,
    /** The accent for ACCENT states: brand orange, or the wallpaper's colour. */
    accent: Color = IslandColors.BrandAccent,
) {
    val bubbleSlot by animateDpAsState(
        targetValue = if (secondary != null) BubbleSize + BubbleGap else 0.dp,
        animationSpec = OyiMotion.defaultSpatial(),
        label = "bubbleSlot",
    )
    // Keep showing the last one while the bubble animates away.
    val lastSecondaryHolder = remember { arrayOf(secondary ?: IslandState()) }
    if (secondary != null) lastSecondaryHolder[0] = secondary
    val lastSecondary = lastSecondaryHolder[0]
    // The island stays centred (on the camera, in the overlay): the bubble's
    // slot is mirrored by an empty one on the other side.
    Row(modifier, verticalAlignment = Alignment.Top) {
        Spacer(Modifier.width(bubbleSlot))
        Island(state, idle, level, onAction, onLongPress, inApp, faces, initiallyExpanded, secondary != null, cutout, accent)
        BubbleSlot(
            width = bubbleSlot,
            visible = secondary != null,
            state = lastSecondary,
            faces = faces,
            accent = accent,
            height = compactHeight(cutout),
            onTap = { onAction(IslandAction.SWAP) },
        )
    }
}

/** The second bubble, kept out of the Row so AnimatedVisibility is the plain one. */
@Composable
private fun BubbleSlot(
    width: Dp,
    visible: Boolean,
    state: IslandState,
    faces: Boolean,
    accent: Color,
    height: Dp,
    onTap: () -> Unit,
) {
    Box(Modifier.width(width), contentAlignment = Alignment.CenterEnd) {
        AnimatedVisibility(
            visible = visible,
            enter = scaleIn(OyiMotion.bouncy(), initialScale = 0.2f) + fadeIn(OyiMotion.fastEffects()) +
                expandHorizontally(OyiMotion.defaultSpatial(), expandFrom = Alignment.Start),
            exit = scaleOut(OyiMotion.fastEffects(), targetScale = 0.4f) + fadeOut(OyiMotion.fastEffects()) +
                shrinkHorizontally(OyiMotion.defaultSpatial(), shrinkTowards = Alignment.Start),
        ) {
            SecondaryBubble(state, faces, accent, height, onTap)
        }
    }
}

private val BubbleSize = 38.dp
private val BubbleGap = 6.dp

private fun compactHeight(cutout: DpSize): Dp =
    if (cutout.height > 0.dp) maxOf(40.dp, cutout.height + 12.dp) else 40.dp

@Composable
private fun Island(
    state: IslandState,
    idle: IslandIdleInfo,
    level: Float,
    onAction: (IslandAction) -> Unit,
    onLongPress: () -> Unit,
    inApp: Boolean,
    faces: Boolean,
    initiallyExpanded: Boolean,
    canSwap: Boolean,
    cutout: DpSize,
    accent: Color,
) {
    var expanded by remember { mutableStateOf(initiallyExpanded) }
    val haptics = LocalHapticFeedback.current
    val still = LocalInspectionMode.current

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
    val tone by animateColorAsState(
        targetValue = IslandColors.tone(spec.tone, accent),
        animationSpec = OyiMotion.defaultEffects(),
        label = "tone",
    )
    val corner by animateDpAsState(
        targetValue = if (spec.size == IslandSize.EXPANDED) 32.dp else compactHeight(cutout) / 2,
        animationSpec = OyiMotion.defaultSpatial(),
        label = "corner",
    )
    val shape = RoundedCornerShape(corner)
    val elevation by animateDpAsState(
        targetValue = if (spec.size == IslandSize.REST) 8.dp else 20.dp,
        animationSpec = OyiMotion.defaultEffects(),
        label = "elevation",
    )

    // The light that runs around the rim while work is under way.
    val rimPhase = if (spec.progressRim && !still) {
        val transition = rememberInfiniteTransition(label = "rim")
        val value by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(1_600, easing = LinearEasing)),
            label = "rimPhase",
        )
        value
    } else {
        0.18f
    }
    val rimAlpha by animateFloatAsState(
        targetValue = if (spec.progressRim) 1f else 0f,
        animationSpec = OyiMotion.slowEffects(),
        label = "rimAlpha",
    )

    Box(
        modifier = Modifier
            .semantics {
                liveRegion = LiveRegionMode.Polite
                contentDescription = listOf(spec.title, spec.detail).filter { it.isNotBlank() }.joinToString(". ")
            }
            .liquidGlass(
                shape = shape,
                fill = SolidColor(IslandColors.Shell),
                backdrop = null,
                elevation = elevation,
                rim = IslandColors.Rim,
            )
            .drawWithContent {
                drawContent()
                if (rimAlpha > 0f) {
                    drawRimLight(shape.createOutline(size, layoutDirection, this), rimPhase, tone, rimAlpha)
                }
            }
            .pointerInput(spec.expandable, spec.dismissable, spec.doubleTap) {
                detectTapGestures(
                    onTap = {
                        when {
                            spec.expandable -> expanded = !expanded
                            spec.dismissable -> onAction(IslandAction.DISMISS)
                        }
                    },
                    onDoubleTap = spec.doubleTap?.let { action ->
                        { _: Offset ->
                            haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            onAction(action)
                        }
                    },
                    onLongPress = {
                        haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                        onLongPress()
                    },
                )
            }
            .pointerInput(spec.dismissable, spec.expandable) {
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
            }
            .pointerInput(canSwap) {
                if (!canSwap) return@pointerInput
                var dragged = 0f
                detectHorizontalDragGestures(
                    onDragStart = { dragged = 0f },
                    onHorizontalDrag = { change, amount ->
                        dragged += amount
                        change.consume()
                    },
                    onDragEnd = {
                        if (abs(dragged) > 48f) {
                            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                            onAction(IslandAction.SWAP)
                        }
                    },
                )
            },
    ) {
        AnimatedContent(
            targetState = spec,
            contentKey = { Triple(it.size, it.lead, it.actions) },
            transitionSpec = {
                // Shape first, content second.
                (fadeIn(tween(200, delayMillis = 120)) +
                    scaleIn(OyiMotion.fastSpatial(), initialScale = 0.9f))
                    .togetherWith(fadeOut(OyiMotion.fastEffects()) + scaleOut(OyiMotion.fastEffects(), targetScale = 0.96f))
                    .using(SizeTransform(clip = true) { _, _ -> OyiMotion.defaultSpatial() })
            },
            label = "island",
        ) { target ->
            CompositionLocalProvider(LocalIslandFaces provides faces) {
                when (target.size) {
                    IslandSize.REST -> RestPill(target, idle, tone, cutout)
                    IslandSize.COMPACT -> CompactPill(target, state, level, tone, cutout)
                    IslandSize.EXPANDED -> ExpandedCard(target, state, level, tone, cutout, onAction)
                }
            }
        }
    }
}

/** A comet of light running around the outline; [phase] goes 0..1. */
private fun DrawScope.drawRimLight(outline: Outline, phase: Float, color: Color, alpha: Float) {
    val path = Path().apply {
        when (outline) {
            is Outline.Rounded -> addRoundRect(outline.roundRect)
            is Outline.Rectangle -> addRect(outline.rect)
            is Outline.Generic -> addPath(outline.path)
        }
    }
    val measure = PathMeasure().apply { setPath(path, true) }
    val length = measure.length
    if (length <= 0f) return
    val stroke = Stroke(width = 2.5.dp.toPx(), cap = StrokeCap.Round)
    fun segment(from: Float, span: Float, a: Float) {
        val start = ((from % length) + length) % length
        val end = start + span
        val piece = Path()
        if (end <= length) {
            measure.getSegment(start, end, piece, true)
        } else {
            measure.getSegment(start, length, piece, true)
            measure.getSegment(0f, end - length, piece, true)
        }
        drawPath(piece, color.copy(alpha = a * alpha), style = stroke)
    }
    val head = phase * length
    // A soft tail, then the bright head.
    segment(head - length * 0.22f, length * 0.16f, 0.22f)
    segment(head - length * 0.08f, length * 0.08f, 0.9f)
}

// ------------------------------------------------------------------ sizes

/**
 * Lays out [start] and [end] on either side of the camera hole. With no
 * cutout they simply sit next to each other.
 */
@Composable
private fun AroundCamera(
    cutout: DpSize,
    height: Dp,
    modifier: Modifier = Modifier,
    start: @Composable () -> Unit,
    end: @Composable () -> Unit,
) {
    Row(modifier.height(height), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) { start() }
        Spacer(Modifier.width(cutout.width + 12.dp))
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterEnd) { end() }
    }
}

@Composable
private fun RestPill(spec: IslandSpec, idle: IslandIdleInfo, tone: Color, cutout: DpSize) {
    val faces = LocalIslandFaces.current
    val lead: @Composable () -> Unit = {
        if (faces) {
            // The face, with the link state as a small dot on its shoulder.
            // The face, with the link state as a small dot beside it (never on it).
            Row(verticalAlignment = Alignment.CenterVertically) {
                Face(spec.face, spec.motion, size = 30.dp)
                Spacer(Modifier.width(5.dp))
                LinkDot(tone, pulsing = spec.tone == IslandTone.WARN, size = 6.dp)
            }
        } else {
            LinkDot(tone, pulsing = spec.tone == IslandTone.WARN)
        }
    }
    val clock: @Composable () -> Unit = {
        if (spec.trail == IslandTrail.CLOCK) {
            Text(
                text = formatDuration(idle.sessionMs),
                color = IslandColors.Cream,
                fontSize = 11.5.sp,
                fontFeatureSettings = TabularNumbers,
                maxLines = 1,
            )
        }
    }
    if (cutout.width > 0.dp) {
        // Around the camera: face on one side, session clock on the other.
        AroundCamera(
            cutout = cutout,
            height = compactHeight(cutout),
            modifier = Modifier
                .width(cutout.width + 128.dp)
                .padding(horizontal = 6.dp),
            start = lead,
            end = clock,
        )
        return
    }
    Row(
        modifier = Modifier
            .height(34.dp)
            .widthIn(min = 112.dp, max = 236.dp)
            .padding(start = if (faces) 6.dp else 14.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        lead()
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
            clock()
        }
    }
}

@Composable
private fun CompactPill(spec: IslandSpec, state: IslandState, level: Float, tone: Color, cutout: DpSize) {
    val lead: @Composable () -> Unit = {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(32.dp), contentAlignment = Alignment.Center) {
                if (LocalIslandFaces.current) {
                    Face(spec.face, spec.motion, size = 34.dp)
                } else {
                    Lead(spec.lead, tone, iconSize = 18.dp)
                }
            }
            Spacer(Modifier.width(10.dp))
            Text(
                text = spec.title,
                color = IslandColors.Ink,
                fontSize = 14.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
    if (cutout.width > 0.dp) {
        AroundCamera(
            cutout = cutout,
            height = compactHeight(cutout),
            modifier = Modifier
                .widthIn(max = 320.dp)
                .fillMaxWidth()
                .padding(start = 4.dp, end = 14.dp),
            start = lead,
            end = { Trail(spec, state, level, tone) },
        )
        return
    }
    Row(
        modifier = Modifier
            .height(40.dp)
            .widthIn(max = 300.dp)
            .fillMaxWidth()
            .padding(start = 4.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.weight(1f)) { lead() }
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
    cutout: DpSize,
    onAction: (IslandAction) -> Unit,
) {
    Column(
        modifier = Modifier
            .widthIn(max = 352.dp)
            .fillMaxWidth()
            // Keep the first line clear of the camera hole.
            .padding(start = 14.dp, end = 16.dp, top = 14.dp + if (cutout.height > 0.dp) cutout.height + 4.dp else 0.dp, bottom = 14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(modifier = Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                // An expressive blob behind the face, morphing while OYI works.
                if (spec.progressRim) {
                    MorphingLoader(tone.copy(alpha = 0.22f), size = 50.dp, periodMs = 900)
                } else {
                    Box(
                        Modifier
                            .matchParentSize()
                            .clip(CircleShape)
                            .background(tone.copy(alpha = 0.18f)),
                    )
                }
                if (LocalIslandFaces.current) {
                    // A little bigger than its circle, so it pops out of it.
                    Face(spec.face, spec.motion, size = 56.dp)
                } else {
                    Lead(spec.lead, tone, iconSize = 22.dp)
                }
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                if (spec.badge.isNotBlank()) {
                    Text(
                        text = spec.badge,
                        color = tone,
                        fontSize = 10.sp,
                        fontWeight = FontWeight.Black,
                        letterSpacing = 1.sp,
                        maxLines = 1,
                    )
                    Spacer(Modifier.height(2.dp))
                }
                Text(
                    text = spec.title,
                    color = IslandColors.Ink,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.ExtraBold,
                    lineHeight = 20.sp,
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
                modifier = Modifier.padding(start = 60.dp, top = 6.dp),
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

/** The activity running behind the shown one: its face in a small glass bubble. */
@Composable
private fun SecondaryBubble(state: IslandState, faces: Boolean, accent: Color, height: Dp, onTap: () -> Unit) {
    val spec = islandSpec(state, IslandIdleInfo(), expanded = false)
    val tone = IslandColors.tone(spec.tone, accent)
    Box(
        modifier = Modifier
            .padding(top = (height - BubbleSize) / 2)
            .size(BubbleSize)
            .liquidGlass(CircleShape, SolidColor(IslandColors.Shell), backdrop = null, elevation = 10.dp, rim = IslandColors.Rim)
            .clickable(onClick = onTap)
            .semantics { contentDescription = "Also running: ${spec.title}. Tap to switch." },
        contentAlignment = Alignment.Center,
    ) {
        CompositionLocalProvider(LocalIslandFaces provides faces) {
            if (faces) Face(spec.face, spec.motion, size = 32.dp) else Lead(spec.lead, tone, iconSize = 16.dp)
        }
        // A tiny tone dot so the bubble says what kind of thing it is.
        Box(
            Modifier
                .align(Alignment.TopEnd)
                .padding(3.dp)
                .size(8.dp)
                .clip(CircleShape)
                .background(IslandColors.Shell)
                .padding(1.5.dp)
                .clip(CircleShape)
                .background(tone),
        )
    }
}

// ------------------------------------------------------------- pieces

private val LocalIslandFaces = staticCompositionLocalOf { true }

@DrawableRes
private fun faceRes(face: IslandFace): Int = when (face) {
    IslandFace.NEUTRAL -> R.drawable.oyi_face_neutral
    IslandFace.COOL -> R.drawable.oyi_face_cool
    IslandFace.THINKING -> R.drawable.oyi_face_thinking
    IslandFace.LAUGHING -> R.drawable.oyi_face_laughing
    IslandFace.TALKING -> R.drawable.oyi_face_talking
    IslandFace.CODING -> R.drawable.oyi_face_coding
    IslandFace.IDEA -> R.drawable.oyi_face_idea
    IslandFace.SLEEPING -> R.drawable.oyi_face_sleeping
    IslandFace.SCREENS -> R.drawable.oyi_face_screens
}

/**
 * One expression sticker. It pops in with a bouncy spring when it first
 * shows, swaps with a pop when the state changes, and then moves the way
 * its state does ([IslandMotion]).
 */
@Composable
private fun Face(face: IslandFace, motion: IslandMotion, size: Dp) {
    val still = LocalInspectionMode.current
    val pop = remember { Animatable(if (still) 1f else 0.35f) }
    LaunchedEffect(Unit) {
        if (!still) pop.animateTo(1f, OyiMotion.bouncy())
    }

    // One-shot kicks: a bounce when something lands, a shake when OYI needs you.
    val kick = remember { Animatable(0f) }
    LaunchedEffect(motion) {
        if (still) return@LaunchedEffect
        when (motion) {
            IslandMotion.BOUNCE -> {
                kick.snapTo(1f)
                kick.animateTo(0f, OyiMotion.bouncy())
            }
            IslandMotion.SHAKE -> {
                kick.snapTo(0f)
                kick.animateTo(
                    0f,
                    keyframes {
                        durationMillis = 520
                        -1f at 60
                        1f at 150
                        -0.7f at 240
                        0.5f at 330
                        -0.25f at 420
                    },
                )
            }
            else -> kick.snapTo(0f)
        }
    }

    // A slow loop for the continuous motions.
    val loops = motion == IslandMotion.BREATHE || motion == IslandMotion.WOBBLE ||
        motion == IslandMotion.TYPE || motion == IslandMotion.PULSE
    val t = if (loops && !still) {
        val transition = rememberInfiniteTransition(label = "faceLoop")
        val value by transition.animateFloat(
            initialValue = 0f,
            targetValue = 1f,
            animationSpec = infiniteRepeatable(tween(2_400, easing = LinearEasing)),
            label = "faceT",
        )
        value
    } else {
        0f
    }
    val wave = (2 * PI * t).toFloat()

    AnimatedContent(
        targetState = face,
        transitionSpec = {
            (scaleIn(OyiMotion.bouncy(), initialScale = 0.3f) + fadeIn(tween(120)))
                .togetherWith(scaleOut(tween(140), targetScale = 0.5f) + fadeOut(tween(110)))
                .using(SizeTransform(clip = false))
        },
        modifier = Modifier.graphicsLayer {
            var s = pop.value
            var rotation = 0f
            var dy = 0f
            when (motion) {
                IslandMotion.BREATHE -> s *= 1f + 0.045f * sin(wave)
                IslandMotion.WOBBLE -> rotation = 8f * sin(wave * 2)
                IslandMotion.TYPE -> dy = -1.6f * abs(sin(wave * 6)) * density
                IslandMotion.PULSE -> s *= 1f + 0.07f * abs(sin(wave * 5))
                IslandMotion.BOUNCE -> s *= 1f + 0.28f * kick.value
                IslandMotion.SHAKE -> rotation = 12f * kick.value
                IslandMotion.STILL -> Unit
            }
            scaleX = s
            scaleY = s
            rotationZ = rotation
            translationY = dy
        },
        label = "face",
    ) { current ->
        Image(
            painter = painterResource(faceRes(current)),
            contentDescription = null,
            modifier = Modifier.requiredSize(size),
        )
    }
}

@Composable
private fun Lead(lead: IslandLead, tone: Color, iconSize: Dp) {
    when (lead) {
        IslandLead.DOT -> LinkDot(tone, pulsing = false, size = 10.dp)
        IslandLead.SPINNER -> MorphingLoader(tone, size = iconSize)
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
        IslandTrail.SPINNER -> MorphingLoader(tone, size = 18.dp)
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
        fontFeatureSettings = TabularNumbers,
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
    val smoothed by animateFloatAsState(level, animationSpec = OyiMotion.fastSpatial(), label = "level")
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

/** A card button with expressive press motion: it squishes and its corners tighten. */
@Composable
private fun ActionPill(action: IslandAction, tone: Color, onAction: (IslandAction) -> Unit) {
    val (label, filled) = when (action) {
        IslandAction.APPROVE -> "Approve" to true
        IslandAction.DENY -> "Deny" to false
        IslandAction.STOP -> "Stop" to true
        IslandAction.DISMISS -> "Close" to false
        IslandAction.OPEN_APP -> "Open OYI" to true
        IslandAction.INTERRUPT -> "Stop talking" to true
        IslandAction.SWAP -> "Switch" to false
    }
    val background = when {
        !filled -> Color(0x1FFFFFFF)
        action == IslandAction.STOP -> Color(0xFFFF6B6B)
        action == IslandAction.OPEN_APP -> IslandColors.Cream
        else -> tone
    }
    PressMorph(onClick = { onAction(action) }, pressedPercent = 28f) { shape ->
        Box(
            modifier = Modifier
                .background(background, shape)
                .border(1.dp, if (filled) Brush.verticalGradient(listOf(Color(0x66FFFFFF), Color(0x00FFFFFF))) else IslandColors.Rim, shape)
                .padding(horizontal = 18.dp, vertical = 9.dp),
        ) {
            Text(
                text = label,
                color = if (filled) IslandColors.InkOnLight else IslandColors.Ink,
                fontSize = 13.sp,
                fontWeight = FontWeight.ExtraBold,
            )
        }
    }
}
