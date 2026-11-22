package ai.oyi.device.ui

import ai.oyi.device.OyiApplication
import ai.oyi.device.R
import ai.oyi.device.data.ISLAND_NUDGE_LIMIT
import ai.oyi.device.service.OyiService
import ai.oyi.device.ui.theme.OyiTheme
import android.content.Intent
import android.graphics.Color as AndroidColor
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import ai.oyi.device.ui.theme.Tabular
import kotlin.math.roundToInt

/**
 * Fitting the island over the camera, live.
 *
 * A see-through screen: whatever is behind it (OYI, or the home screen)
 * is blurred and dimmed, the real overlay island shows on top at the camera,
 * and it moves as the sliders move. No need to leave the app to check.
 */
class IslandFitActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(AndroidColor.TRANSPARENT),
        )
        val container = (application as OyiApplication).container
        val settings = container.settings
        setContent {
            val prefs by settings.settings.collectAsStateWithLifecycle()
            var x by remember { mutableIntStateOf(prefs.islandNudgeXDp) }
            var y by remember { mutableIntStateOf(prefs.islandNudgeYDp) }
            OyiTheme(wallpaperColors = prefs.wallpaperColors) {
                IslandFitContent(
                    x = x,
                    y = y,
                    live = prefs.overlayEnabled && Settings.canDrawOverlays(this),
                    onChange = { nx, ny ->
                        x = nx
                        y = ny
                        settings.setIslandNudge(nx, ny)
                    },
                    onTurnOn = ::turnOnIsland,
                    onDone = ::finish,
                )
            }
        }
    }

    // Let the overlay island show while this screen is up: it is the preview.
    override fun onResume() {
        super.onResume()
        (application as OyiApplication).container.runtime.setAppVisible(false)
    }

    private fun turnOnIsland() {
        if (!Settings.canDrawOverlays(this)) return
        (application as OyiApplication).container.settings.setOverlayEnabled(true)
        runCatching { ContextCompat.startForegroundService(this, Intent(this, OyiService::class.java)) }
    }
}

/** The fit screen's content, without the activity (snapshots use it). */
@Composable
fun IslandFitContent(
    x: Int,
    y: Int,
    live: Boolean,
    onChange: (Int, Int) -> Unit,
    onTurnOn: () -> Unit,
    onDone: () -> Unit,
) {
    Box(Modifier.fillMaxSize()) {
        // A quiet pointer to where to look.
        Column(
            modifier = Modifier
                .align(Alignment.TopCenter)
                .padding(top = 150.dp, start = 40.dp, end = 40.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(
                painterResource(R.drawable.ic_arrow_upward),
                contentDescription = null,
                tint = Oyi.OffWhite.copy(alpha = 0.7f),
                modifier = Modifier.size(22.dp),
            )
            Spacer(Modifier.height(8.dp))
            Text(
                if (live) "Watch the island at the top as you slide" else "The island is off, so there is nothing to line up yet",
                color = Oyi.OffWhite.copy(alpha = 0.8f),
                fontSize = 14.sp,
                lineHeight = 20.sp,
                textAlign = TextAlign.Center,
            )
        }

        // The controls sit low, where the thumb already is.
        OyiCard(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(horizontal = 12.dp, vertical = 12.dp),
            padding = PaddingValues(start = 20.dp, end = 20.dp, top = 22.dp, bottom = 18.dp),
            color = Oyi.Surface.copy(alpha = 0.96f),
        ) {
            Text("Fit the island", style = MaterialTheme.typography.headlineSmall, color = Oyi.OffWhite)
            Spacer(Modifier.height(4.dp))
            Text(
                "Slide until it sits right over your camera. It saves as you go.",
                color = Oyi.Muted,
                fontSize = 14.sp,
                lineHeight = 20.sp,
            )
            if (!live) {
                Spacer(Modifier.height(14.dp))
                OyiButton(
                    text = "Turn on the island",
                    onClick = onTurnOn,
                    kind = ButtonKind.TONAL,
                    icon = R.drawable.ic_bolt,
                    fullWidth = true,
                )
            }
            Spacer(Modifier.height(20.dp))
            SliderLabel("Left or right", when {
                x == 0 -> "Centred"
                x > 0 -> "$x right"
                else -> "${-x} left"
            })
            CenterSlider(x, { onChange(it, y) }, -ISLAND_NUDGE_LIMIT..ISLAND_NUDGE_LIMIT, label = "Left or right")
            Spacer(Modifier.height(14.dp))
            SliderLabel("Up or down", when {
                y == 0 -> "On the camera"
                y > 0 -> "$y down"
                else -> "${-y} up"
            })
            CenterSlider(y, { onChange(x, it) }, -ISLAND_NUDGE_LIMIT..ISLAND_NUDGE_LIMIT, label = "Up or down")
            Spacer(Modifier.height(22.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OyiButton(
                    text = "Reset",
                    onClick = { onChange(0, 0) },
                    kind = ButtonKind.GHOST,
                    enabled = x != 0 || y != 0,
                    modifier = Modifier.weight(1f),
                    fullWidth = true,
                )
                OyiButton(
                    text = "Done",
                    onClick = onDone,
                    icon = R.drawable.ic_check,
                    modifier = Modifier.weight(1f),
                    fullWidth = true,
                )
            }
        }
    }
}

@Composable
private fun SliderLabel(title: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(title, color = Oyi.OffWhite, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
        Text(value, color = Oyi.Muted, fontSize = 13.sp, style = LocalTextStyle.current.merge(Tabular))
    }
}

/**
 * A Material 3 Expressive style slider that starts from the middle: a tall
 * track, the part between centre and the value filled, and a slim handle
 * with a gap either side. Every step ticks; centre ticks harder.
 */
@Composable
internal fun CenterSlider(
    value: Int,
    onChange: (Int) -> Unit,
    range: IntRange,
    label: String,
    modifier: Modifier = Modifier,
    gapColor: Color = Oyi.Surface,
) {
    val haptics = LocalHapticFeedback.current
    val current by rememberUpdatedState(value)
    val emit by rememberUpdatedState(onChange)
    var widthPx by remember { mutableFloatStateOf(1f) }
    val edge = 12.dp
    fun valueAt(px: Float, edgePx: Float): Int {
        val f = ((px - edgePx) / (widthPx - 2 * edgePx).coerceAtLeast(1f)).coerceIn(0f, 1f)
        return (range.first + f * (range.last - range.first)).roundToInt()
    }
    fun set(v: Int) {
        if (v == current) return
        haptics.performHapticFeedback(if (v == 0) HapticFeedbackType.LongPress else HapticFeedbackType.TextHandleMove)
        emit(v)
    }
    Box(
        modifier = modifier
            .fillMaxWidth()
            .height(48.dp)
            .onSizeChanged { widthPx = it.width.toFloat() }
            .semantics {
                contentDescription = label
                stateDescription = "$value"
            }
            .pointerInput(range) {
                detectTapGestures { set(valueAt(it.x, edge.toPx())) }
            }
            .pointerInput(range) {
                detectHorizontalDragGestures { change, _ ->
                    change.consume()
                    set(valueAt(change.position.x, edge.toPx()))
                }
            },
    ) {
        Canvas(Modifier.matchParentSize()) {
            val e = edge.toPx()
            val trackH = 16.dp.toPx()
            val cy = size.height / 2f
            val left = e
            val right = size.width - e
            fun xOf(v: Int) = left + (v - range.first).toFloat() / (range.last - range.first) * (right - left)
            val r = CornerRadius(trackH / 2f, trackH / 2f)
            drawRoundRect(Oyi.Control, Offset(left - e / 2, cy - trackH / 2), Size(right - left + e, trackH), r)
            val zero = xOf(0)
            val at = xOf(value)
            if (value != 0) {
                val from = minOf(zero, at)
                drawRoundRect(Oyi.Orange, Offset(from, cy - trackH / 2), Size(kotlin.math.abs(at - zero), trackH), CornerRadius(4.dp.toPx()))
            }
            // The centre mark.
            // Hidden near the handle, where it would read as a stray dot.
            if (value == 0 || kotlin.math.abs(at - zero) > 14.dp.toPx()) {
                drawCircle(if (value == 0) Oyi.Ink else Oyi.Muted, radius = 2.dp.toPx(), center = Offset(zero, cy))
            }
            // The handle, with a gap cut either side.
            val handleW = 5.dp.toPx()
            val handleH = 40.dp.toPx()
            val gap = 5.dp.toPx()
            drawRect(gapColor, Offset(at - handleW / 2 - gap, cy - trackH / 2), Size(handleW + 2 * gap, trackH))
            drawRoundRect(Oyi.Cream, Offset(at - handleW / 2, cy - handleH / 2), Size(handleW, handleH), CornerRadius(handleW / 2))
        }
    }
}
