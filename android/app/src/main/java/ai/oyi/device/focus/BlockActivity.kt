package ai.oyi.device.focus

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import ai.oyi.device.OyiApplication
import ai.oyi.device.ui.Eyebrow
import ai.oyi.device.ui.FocusRing
import ai.oyi.device.ui.Oyi
import ai.oyi.device.ui.formatClock

/** The screen that takes the place of a blocked app or site. */
class BlockActivity : ComponentActivity() {

    private var label by mutableStateOf("")
    private var blockedPackage by mutableStateOf("")
    private var reason by mutableStateOf(BlockReason.ALWAYS)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        read(intent)
        val container = (application as OyiApplication).container
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(background = Oyi.Black, surface = Oyi.Black)) {
                val state by container.focusStore.state.collectAsStateWithLifecycle()
                val live by container.focusGuard.live.collectAsStateWithLifecycle()
                BlockScreen(
                    label = label,
                    blockedPackage = blockedPackage,
                    reason = reason,
                    state = state,
                    live = live,
                    onLeave = ::leave,
                    onFinishTask = {
                        container.focusStore.endSession()
                        leave()
                    },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        read(intent)
    }

    private fun read(intent: Intent?) {
        label = intent?.getStringExtra(EXTRA_LABEL).orEmpty()
        blockedPackage = intent?.getStringExtra(EXTRA_PACKAGE).orEmpty()
        reason = runCatching { BlockReason.valueOf(intent?.getStringExtra(EXTRA_REASON).orEmpty()) }
            .getOrDefault(BlockReason.ALWAYS)
    }

    private fun leave() {
        runCatching {
            startActivity(
                Intent(Intent.ACTION_MAIN)
                    .addCategory(Intent.CATEGORY_HOME)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            )
        }
        finish()
    }

    companion object {
        const val EXTRA_LABEL = "label"
        const val EXTRA_PACKAGE = "package"
        const val EXTRA_REASON = "reason"
    }
}

@Composable
private fun BlockScreen(
    label: String,
    blockedPackage: String,
    reason: BlockReason,
    state: FocusState,
    live: FocusLive,
    onLeave: () -> Unit,
    onFinishTask: () -> Unit,
) {
    val session = state.session
    val glow = Brush.radialGradient(
        colors = listOf(Color(0x40F08A65), Color(0x00F08A65)),
        center = Offset(0f, 0f),
        radius = 1100f,
    )
    val detail = when (reason) {
        BlockReason.ALWAYS -> "You set this app to always blocked."
        BlockReason.SITE -> "This site is on your block list."
        BlockReason.LIMIT -> {
            val allowance = state.distraction(blockedPackage)?.dailyLimitMin ?: 0
            if (allowance > 0) {
                "You used all $allowance minutes for today."
            } else {
                "Today's allowance is used up."
            }
        }
        BlockReason.LOCKDOWN -> "Distractions are locked until this block ends."
    }

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Oyi.Black)
            .background(glow),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .systemBarsPadding()
                .padding(horizontal = 28.dp, vertical = 28.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(Oyi.Orange))
                Spacer(Modifier.width(10.dp))
                Eyebrow(if (label.isBlank()) "Blocked" else "Blocked · $label", color = Oyi.OffWhite)
            }

            Spacer(Modifier.weight(1f))

            Text(
                text = NudgeVoice.blocked(state.voice, reason, session?.task.orEmpty()),
                color = Oyi.OffWhite,
                fontSize = 38.sp,
                lineHeight = 44.sp,
                fontWeight = FontWeight.Light,
            )
            Spacer(Modifier.height(14.dp))
            Text(detail, color = Oyi.Muted, fontSize = 15.sp, lineHeight = 21.sp)

            if (session != null) {
                val total = (session.endsAt - session.startedAt).coerceAtLeast(1L)
                val remaining = (session.endsAt - live.now).coerceIn(0L, total)
                Spacer(Modifier.height(30.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    FocusRing(
                        progress = 1f - remaining.toFloat() / total.toFloat(),
                        modifier = Modifier.size(58.dp),
                        strokeWidth = 5.dp,
                    )
                    Spacer(Modifier.width(16.dp))
                    Column {
                        Eyebrow("Due now")
                        Spacer(Modifier.height(3.dp))
                        Text(
                            session.task.ifBlank { "Focus" },
                            color = Oyi.OffWhite,
                            fontSize = 18.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            "${formatClock(remaining)} left",
                            color = Oyi.Muted,
                            fontSize = 13.sp,
                            fontFamily = FontFamily.Monospace,
                        )
                    }
                }
            }

            Spacer(Modifier.weight(1f))

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(Oyi.Pill)
                    .background(Oyi.CreamGlass)
                    .clickable(onClick = onLeave)
                    .padding(vertical = 16.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text("Back to work", color = Oyi.Ink, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            }
            if (session != null) {
                Spacer(Modifier.height(6.dp))
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Oyi.Pill)
                        .clickable(onClick = onFinishTask)
                        .padding(vertical = 14.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("I finished the task", color = Oyi.Muted, fontSize = 14.sp, fontWeight = FontWeight.Medium)
                }
            }
        }
    }
}
