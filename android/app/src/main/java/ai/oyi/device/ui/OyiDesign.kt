package ai.oyi.device.ui

import androidx.compose.foundation.layout.calculateStartPadding
import androidx.compose.foundation.layout.calculateEndPadding
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.animation.core.spring
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalLayoutDirection
import ai.oyi.device.R
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.PressMorph
import ai.oyi.device.ui.theme.Tabular
import androidx.compose.material3.LocalTextStyle
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandHorizontally
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkHorizontally
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.drawable.toBitmap

/**
 * OYI's design tokens.
 *
 * True black (it matches the island and the camera), solid near-black
 * surfaces with a hairline edge, warm off-white text in three strengths, and
 * one accent, used sparingly: orange for the main action and live states,
 * cream for "selected".
 */
internal object Oyi {
    // Surfaces
    val Black = Color(0xFF000000)
    val Surface = Color(0xFF111113)
    val Raised = Color(0xFF19191C)
    val Control = Color(0xFF232327)
    val Hairline = Color(0x14FFFFFF)
    val HairlineStrong = Color(0x24FFFFFF)

    // Text on dark
    val OffWhite = Color(0xFFF5F3EE)
    val Muted = Color(0xFFA7A39B)
    val Faint = Color(0xFF6E6B66)

    // Accent and selection
    val Orange = Color(0xFFF08A65)
    val OrangeDeep = Color(0xFFE07450)
    val OrangeTint = Color(0x26F08A65)
    val Cream = Color(0xFFF7EDD0)
    val Ink = Color(0xFF0A0807)
    val InkSoft = Color(0xFF3D3624)
    val Yellow = Color(0xFFF7E27E)

    // Status
    val Good = Color(0xFF5FD38D)
    val Warn = Color(0xFFF5B547)
    val Bad = Color(0xFFFF6B6B)

    // Shapes
    val Pill = RoundedCornerShape(50)
    val Card = RoundedCornerShape(24.dp)
    val Tile = RoundedCornerShape(12.dp)

    // Used by the block screen.
    val OrangeGlass = Brush.linearGradient(listOf(Color(0xFFFFA584), Orange, OrangeDeep))
    val CreamGlass = Brush.linearGradient(listOf(Color(0xFFFFF8E6), Cream))
}

/** Screen gutter and the gap between cards. */
internal val Gutter = 20.dp
internal val CardGap = 12.dp

/** The floating nav: its height and its gap above the system bar. */
internal val NavHeight = 64.dp
internal val NavBottomMargin = 28.dp

/** Room under a scrolling screen so its last card clears the floating nav. */
internal val NavClearance = NavHeight + NavBottomMargin + 28.dp

@Composable
internal fun BottomClearance() {
    Spacer(Modifier.navigationBarsPadding().height(NavClearance))
}

/** A flat card: solid surface, hairline edge, no glow. */
@Composable
internal fun OyiCard(
    modifier: Modifier = Modifier,
    padding: PaddingValues = PaddingValues(20.dp),
    color: Color = Oyi.Surface,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(Oyi.Card)
            .background(color)
            .border(1.dp, Oyi.Hairline, Oyi.Card)
            .padding(padding),
        content = content,
    )
}

/** A screen's big title, with an optional quiet line under it. */
@Composable
internal fun ScreenTitle(title: String, subtitle: String = "", trailing: @Composable () -> Unit = {}) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 8.dp, bottom = 16.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        Column(Modifier.weight(1f)) {
            Text(
                text = title,
                style = androidx.compose.material3.MaterialTheme.typography.displaySmall,
                color = Oyi.OffWhite,
            )
            if (subtitle.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(subtitle, color = Oyi.Muted, fontSize = 14.sp, lineHeight = 19.sp)
            }
        }
        trailing()
    }
}

/** The small caption above a section. */
@Composable
internal fun Eyebrow(text: String, color: Color = Oyi.Faint, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        color = color,
        fontSize = 11.sp,
        fontWeight = FontWeight.Bold,
        letterSpacing = 1.2.sp,
        modifier = modifier,
    )
}

enum class ButtonKind { PRIMARY, TONAL, CREAM, GHOST, DANGER }

/**
 * The one button. Primary is solid orange and there should be one per
 * screen area; tonal is a quiet grey; cream is "selected/confirm" on dark.
 * It squishes and squares off a little under the finger, with a tick of
 * haptic feedback.
 */
@Composable
internal fun OyiButton(
    text: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    kind: ButtonKind = ButtonKind.PRIMARY,
    @DrawableRes icon: Int? = null,
    enabled: Boolean = true,
    small: Boolean = false,
    fullWidth: Boolean = false,
) {
    val haptics = LocalHapticFeedback.current
    val (bg, fg) = when (kind) {
        ButtonKind.PRIMARY -> Oyi.Orange to Oyi.Ink
        ButtonKind.TONAL -> Oyi.Control to Oyi.OffWhite
        ButtonKind.CREAM -> Oyi.Cream to Oyi.Ink
        ButtonKind.GHOST -> Color.Transparent to Oyi.OffWhite
        ButtonKind.DANGER -> Oyi.Bad.copy(alpha = 0.16f) to Oyi.Bad
    }
    PressMorph(
        onClick = {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onClick()
        },
        enabled = enabled,
        modifier = modifier
            .then(if (fullWidth) Modifier.fillMaxWidth() else Modifier)
            .alpha(if (enabled) 1f else 0.4f),
        pressedPercent = 28f,
    ) { shape ->
        Row(
            modifier = Modifier
                .then(if (fullWidth) Modifier.fillMaxWidth() else Modifier)
                .heightIn(min = if (small) 36.dp else 48.dp)
                .background(bg, shape)
                .then(if (kind == ButtonKind.GHOST) Modifier.border(1.dp, Oyi.HairlineStrong, shape) else Modifier)
                .padding(horizontal = if (small) 14.dp else 22.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Center,
        ) {
            if (icon != null) {
                Icon(painterResource(icon), contentDescription = null, tint = fg, modifier = Modifier.size(if (small) 16.dp else 18.dp))
                Spacer(Modifier.width(6.dp))
            }
            Text(
                text = text,
                color = fg,
                fontSize = if (small) 13.sp else 15.sp,
                fontWeight = FontWeight.Bold,
                maxLines = 1,
            )
        }
    }
}

/**
 * A choice chip. Selected chips go cream and grow a check, so the state
 * never relies on colour alone.
 */
@Composable
internal fun Pill(
    text: String,
    selected: Boolean = false,
    enabled: Boolean = true,
    @Suppress("UNUSED_PARAMETER") onDark: Boolean = true,
    onClick: () -> Unit,
) {
    val haptics = LocalHapticFeedback.current
    val bg by animateColorAsState(
        if (selected) Oyi.Cream else Oyi.Raised,
        OyiMotion.defaultEffects(),
        label = "chipBg",
    )
    val fg by animateColorAsState(
        if (selected) Oyi.Ink else Oyi.OffWhite,
        OyiMotion.defaultEffects(),
        label = "chipFg",
    )
    PressMorph(
        onClick = {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onClick()
        },
        modifier = Modifier.alpha(if (enabled) 1f else 0.4f),
        pressedPercent = 30f,
    ) { shape ->
        Row(
            modifier = Modifier
                .height(36.dp)
                .background(bg, shape)
                .border(1.dp, if (selected) Color.Transparent else Oyi.Hairline, shape)
                .padding(start = if (selected) 10.dp else 14.dp, end = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            AnimatedVisibility(
                visible = selected,
                enter = fadeIn(OyiMotion.fastEffects()) + expandHorizontally(OyiMotion.fastSpatial()),
                exit = fadeOut(OyiMotion.fastEffects()) + shrinkHorizontally(OyiMotion.fastSpatial()),
            ) {
                Row {
                    Icon(painterResource(R.drawable.ic_check), null, tint = fg, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(4.dp))
                }
            }
            Text(text, color = fg, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
        }
    }
}

/**
 * A connected button group (Material 3 Expressive): equal segments, round
 * outer ends, tight inner corners, and the selected one rounds out fully.
 */
@Composable
internal fun SegmentedButtons(
    options: List<String>,
    selected: Int,
    onSelect: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val haptics = LocalHapticFeedback.current
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(3.dp)) {
        options.forEachIndexed { index, label ->
            val isSelected = index == selected
            val first = index == 0
            val last = index == options.lastIndex
            val inner by animateDpAsState(if (isSelected) 24.dp else 6.dp, OyiMotion.fastSpatial(), label = "segInner")
            val shape = RoundedCornerShape(
                topStart = if (first) 24.dp else inner,
                bottomStart = if (first) 24.dp else inner,
                topEnd = if (last) 24.dp else inner,
                bottomEnd = if (last) 24.dp else inner,
            )
            val bg by animateColorAsState(if (isSelected) Oyi.Cream else Oyi.Raised, OyiMotion.defaultEffects(), label = "segBg")
            Box(
                modifier = Modifier
                    .weight(1f)
                    .height(44.dp)
                    .clip(shape)
                    .background(bg)
                    .clickable {
                        haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                        onSelect(index)
                    },
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = label,
                    color = if (isSelected) Oyi.Ink else Oyi.OffWhite,
                    fontSize = 14.sp,
                    fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
                    style = LocalTextStyle.current.merge(Tabular),
                )
            }
        }
    }
}

/** A tinted square behind a row's icon. */
@Composable
internal fun IconTile(@DrawableRes icon: Int, tint: Color = Oyi.OffWhite, background: Color = Oyi.Control, size: Dp = 36.dp) {
    Box(
        modifier = Modifier
            .size(size)
            .clip(Oyi.Tile)
            .background(background),
        contentAlignment = Alignment.Center,
    ) {
        Icon(painterResource(icon), contentDescription = null, tint = tint, modifier = Modifier.size(size * 0.55f))
    }
}

/** A settings-style row: leading tile, two lines, and whatever goes on the right. */
@Composable
internal fun ListRow(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String = "",
    leading: (@Composable () -> Unit)? = null,
    onClick: (() -> Unit)? = null,
    trailing: @Composable () -> Unit = {},
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .then(if (onClick != null) Modifier.clip(RoundedCornerShape(14.dp)).clickable(onClick = onClick) else Modifier)
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(14.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, color = Oyi.OffWhite, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (subtitle.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(subtitle, color = Oyi.Muted, fontSize = 13.sp, lineHeight = 17.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        Spacer(Modifier.width(12.dp))
        trailing()
    }
}

/** A card's heading row: icon tile, title, a quiet line, and an action. */
@Composable
internal fun CardHeader(
    title: String,
    @DrawableRes icon: Int,
    subtitle: String = "",
    iconTint: Color = Oyi.OffWhite,
    iconBackground: Color = Oyi.Control,
    trailing: @Composable () -> Unit = {},
) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        IconTile(icon, tint = iconTint, background = iconBackground, size = 40.dp)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = Oyi.OffWhite, fontSize = 17.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
            if (subtitle.isNotBlank()) {
                Spacer(Modifier.height(1.dp))
                Text(subtitle, color = Oyi.Muted, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        trailing()
    }
}

/** Quiet explanatory text under a control. */
@Composable
internal fun Hint(text: String, modifier: Modifier = Modifier) {
    Text(text, color = Oyi.Faint, fontSize = 12.5.sp, lineHeight = 18.sp, modifier = modifier)
}

/** A single-line text field on a raised pill, with an optional leading icon. */
@Composable
internal fun OyiTextField(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    modifier: Modifier = Modifier,
    @DrawableRes icon: Int? = null,
    password: Boolean = false,
    keyboardOptions: KeyboardOptions = KeyboardOptions.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
) {
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        singleLine = true,
        textStyle = TextStyle(color = Oyi.OffWhite, fontSize = 15.sp, fontFamily = ai.oyi.device.ui.theme.OyiSans),
        cursorBrush = SolidColor(Oyi.Orange),
        visualTransformation = if (password) PasswordVisualTransformation() else VisualTransformation.None,
        keyboardOptions = keyboardOptions,
        keyboardActions = keyboardActions,
        modifier = modifier,
        decorationBox = { inner ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .clip(Oyi.Pill)
                    .background(Oyi.Raised)
                    .border(1.dp, Oyi.Hairline, Oyi.Pill)
                    .padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (icon != null) {
                    Icon(painterResource(icon), null, tint = Oyi.Faint, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(10.dp))
                }
                Box(Modifier.weight(1f)) {
                    if (value.isEmpty()) Text(placeholder, color = Oyi.Faint, fontSize = 15.sp, maxLines = 1)
                    inner()
                }
            }
        },
    )
}

/** A round icon-only button, 40dp, on a raised fill. */
@Composable
internal fun IconButton(
    @DrawableRes icon: Int,
    contentDescription: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    tint: Color = Oyi.Muted,
    background: Color = Color.Transparent,
    enabled: Boolean = true,
) {
    val haptics = LocalHapticFeedback.current
    PressMorph(
        onClick = {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onClick()
        },
        enabled = enabled,
        modifier = modifier.size(40.dp).alpha(if (enabled) 1f else 0.35f),
    ) { shape ->
        Box(
            Modifier.size(40.dp).background(background, shape).semantics { this.contentDescription = contentDescription },
            contentAlignment = Alignment.Center,
        ) {
            Icon(painterResource(icon), null, tint = tint, modifier = Modifier.size(20.dp))
        }
    }
}

/** A hairline between rows, inset past the leading tile. */
@Composable
internal fun RowDivider(inset: Dp = 50.dp) {
    Box(
        Modifier
            .padding(start = inset)
            .fillMaxWidth()
            .height(1.dp)
            .background(Oyi.Hairline),
    )
}

/** A dot and a word: "Connected", "Allowed", "Off". */
@Composable
internal fun StatusLabel(text: String, color: Color) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(6.dp))
        Text(text, color = color, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** The brand switch: orange when on, quiet grey when off. */
@Composable
internal fun OyiSwitch(checked: Boolean, onCheckedChange: (Boolean) -> Unit) {
    val haptics = LocalHapticFeedback.current
    Switch(
        checked = checked,
        onCheckedChange = {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onCheckedChange(it)
        },
        colors = SwitchDefaults.colors(
            checkedThumbColor = Oyi.Ink,
            checkedTrackColor = Oyi.Orange,
            checkedBorderColor = Color.Transparent,
            uncheckedThumbColor = Oyi.Muted,
            uncheckedTrackColor = Oyi.Control,
            uncheckedBorderColor = Oyi.HairlineStrong,
        ),
    )
}

/** − value + : a small nudge control. */
@Composable
internal fun Stepper(value: Int, onChange: (Int) -> Unit, range: IntRange, unit: String = "") {
    Row(verticalAlignment = Alignment.CenterVertically) {
        StepButton(R.drawable.ic_remove, enabled = value > range.first) { onChange(value - 1) }
        Text(
            text = (if (value > 0) "+$value" else "$value") + unit,
            color = if (value == 0) Oyi.Muted else Oyi.OffWhite,
            fontSize = 14.sp,
            fontWeight = FontWeight.SemiBold,
            style = LocalTextStyle.current.merge(Tabular),
            modifier = Modifier.width(52.dp),
            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
        )
        StepButton(R.drawable.ic_add, enabled = value < range.last) { onChange(value + 1) }
    }
}

@Composable
private fun StepButton(@DrawableRes icon: Int, enabled: Boolean, onClick: () -> Unit) {
    val haptics = LocalHapticFeedback.current
    PressMorph(
        onClick = {
            haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
            onClick()
        },
        enabled = enabled,
        modifier = Modifier.size(36.dp).alpha(if (enabled) 1f else 0.35f),
    ) { shape ->
        Box(Modifier.size(36.dp).background(Oyi.Control, shape), contentAlignment = Alignment.Center) {
            Icon(painterResource(icon), null, tint = Oyi.OffWhite, modifier = Modifier.size(18.dp))
        }
    }
}

/** The progress ring of a focus block. [progress] runs from 0 to 1. */
@Composable
internal fun FocusRing(
    progress: Float,
    modifier: Modifier = Modifier,
    strokeWidth: Dp = 8.dp,
    color: Color = Oyi.Orange,
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
                color = Color(0xFF26262B),
                startAngle = 0f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = topLeft,
                size = arcSize,
                style = Stroke(width = stroke),
            )
            drawArc(
                color = color,
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

/** Decoded app icons, shared by every list, so each is loaded once. */
private val IconCache = android.util.LruCache<String, androidx.compose.ui.graphics.ImageBitmap>(64)

/**
 * An installed app's own icon, with a quiet letter tile when it has none.
 * Icons load off the main thread (decoding them there made tab switches
 * stutter); the letter tile shows for the first frame or two.
 */
@Composable
internal fun AppIcon(packageName: String, size: Dp = 40.dp, label: String = "") {
    val context = LocalContext.current
    val bitmap by androidx.compose.runtime.produceState(IconCache.get(packageName), packageName) {
        if (value != null) return@produceState
        value = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
            runCatching {
                context.packageManager.getApplicationIcon(packageName).toBitmap(128, 128).asImageBitmap()
            }.getOrNull()
        }?.also { IconCache.put(packageName, it) }
    }
    val shape = RoundedCornerShape(size * 0.3f)
    val icon = bitmap
    if (icon != null) {
        Image(bitmap = icon, contentDescription = null, modifier = Modifier.size(size).clip(shape))
    } else {
        Box(
            modifier = Modifier.size(size).clip(shape).background(Oyi.Control),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = label.take(1).uppercase(),
                color = Oyi.Muted,
                fontSize = (size.value * 0.4f).sp,
                fontWeight = FontWeight.Bold,
            )
        }
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

/**
 * A card whose job is finished folds down to its header, so finished setup
 * stops taking space from what still needs doing. A chevron says there is
 * more; tapping the header opens it. It folds again by itself the moment it
 * becomes done, and opens by itself if it stops being done.
 */
@Composable
internal fun FoldingCard(
    done: Boolean,
    title: String,
    @DrawableRes icon: Int,
    subtitle: String = "",
    iconTint: Color = Oyi.OffWhite,
    iconBackground: Color = Oyi.Control,
    padding: PaddingValues = PaddingValues(20.dp),
    trailing: @Composable () -> Unit = {},
    content: @Composable ColumnScope.() -> Unit,
) {
    var open by rememberSaveable(done) { mutableStateOf(!done) }
    val haptics = LocalHapticFeedback.current
    val turn by animateFloatAsState(if (open) 90f else 0f, spring(dampingRatio = 0.7f, stiffness = 700f), label = "foldChevron")
    val layout = LocalLayoutDirection.current
    // Folded, the header sits evenly in the card; open, the card keeps its own bottom.
    val bottom by animateDpAsState(
        if (open) padding.calculateBottomPadding() else padding.calculateTopPadding(),
        spring(dampingRatio = 0.9f, stiffness = 700f),
        label = "foldBottom",
    )
    OyiCard(
        padding = PaddingValues(
            start = padding.calculateStartPadding(layout),
            end = padding.calculateEndPadding(layout),
            top = padding.calculateTopPadding(),
            bottom = bottom,
        ),
    ) {
        Box(
            Modifier
                .then(
                    if (done) {
                        Modifier
                            .clip(RoundedCornerShape(14.dp))
                            .clickable(
                                interactionSource = remember { MutableInteractionSource() },
                                indication = null,
                                onClickLabel = if (open) "Fold" else "Open",
                            ) {
                                haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                                open = !open
                            }
                    } else {
                        Modifier
                    },
                ),
        ) {
            CardHeader(title = title, icon = icon, subtitle = subtitle, iconTint = iconTint, iconBackground = iconBackground) {
                trailing()
                if (done) {
                    Spacer(Modifier.width(6.dp))
                    Icon(
                        painterResource(R.drawable.ic_chevron_right),
                        contentDescription = null,
                        tint = Oyi.Faint,
                        modifier = Modifier.size(22.dp).graphicsLayer { rotationZ = turn },
                    )
                }
            }
        }
        AnimatedVisibility(
            visible = open,
            enter = expandVertically(spring(dampingRatio = 0.8f, stiffness = 520f), expandFrom = Alignment.Top) +
                fadeIn(tween(160, delayMillis = 40)),
            exit = shrinkVertically(spring(dampingRatio = 0.95f, stiffness = 900f), shrinkTowards = Alignment.Top) +
                fadeOut(tween(90)),
        ) {
            Column(content = content)
        }
    }
}
