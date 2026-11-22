package ai.oyi.device.focus

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
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
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import ai.oyi.device.OyiApplication
import ai.oyi.device.R
import ai.oyi.device.ui.ButtonKind
import ai.oyi.device.ui.OyiButton
import ai.oyi.device.ui.theme.TabularNumbers
import androidx.compose.foundation.Image
import androidx.compose.ui.res.painterResource
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
            ai.oyi.device.ui.theme.OyiTheme {
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
            .background(Oyi.Black),
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
                Eyebrow(if (label.isBlank()) "Blocked" else "Blocked · $label", color = Oyi.Muted)
            }

            Spacer(Modifier.weight(1f))

            Image(
                painterResource(R.drawable.oyi_face_cool),
                contentDescription = null,
                modifier = Modifier.size(88.dp),
            )
            Spacer(Modifier.height(24.dp))
            Text(
                text = NudgeVoice.blocked(state.voice, reason, session?.task.orEmpty()),
                style = MaterialTheme.typography.displaySmall,
                color = Oyi.OffWhite,
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
                        modifier = Modifier.size(52.dp),
                        strokeWidth = 6.dp,
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
                            fontFeatureSettings = TabularNumbers,
                        )
                    }
                }
            }

            Spacer(Modifier.weight(1f))

            OyiButton(text = "Back to work", onClick = onLeave, kind = ButtonKind.CREAM, fullWidth = true)
            if (session != null) {
                Spacer(Modifier.height(8.dp))
                OyiButton(
                    text = "I finished the task",
                    onClick = onFinishTask,
                    kind = ButtonKind.GHOST,
                    icon = R.drawable.ic_check,
                    fullWidth = true,
                )
            }
        }
    }
}
