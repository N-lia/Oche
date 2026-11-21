package ai.oyi.device.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.clickable
import androidx.compose.ui.draw.scale
import androidx.compose.ui.res.painterResource
import ai.oyi.device.R
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

private val PitchBlack = Color(0xFF070708)
private val OffWhite = Color(0xFFF8F6F1)
private val MutedWarmGrey = Color(0xFFA09C94)
private val UserCreamBubble = Color(0xFFF7EDD0)
private val AgentOrangeBubble = Color(0xFFF08A65)
private val DarkInk = Color(0xFF0A0807)
private val OrangeMetaInk = Color(0xFF24140D)
private val CreamMetaInk = Color(0xFF423822)

private val DarkGlassRim = Brush.verticalGradient(
    colors = listOf(Color(0x59FFFFFF), Color(0x14FFFFFF)),
)
private val WarmGlassRim = Brush.verticalGradient(
    colors = listOf(Color(0xB8FFF9F2), Color(0x33FFFFFF)),
)
private val OrangeBubbleBrush = Brush.linearGradient(
    colors = listOf(Color(0xFFFFA584), Color(0xFFF08A65), Color(0xFFE07450)),
)
private val CreamBubbleBrush = Brush.linearGradient(
    colors = listOf(Color(0xFFFFF9EA), Color(0xFFF7EDD0), Color(0xFFEBDDBE)),
)
private val SmokedGlassBrush = Brush.linearGradient(
    colors = listOf(Color(0xD926262D), Color(0xCC141418)),
)

@Composable
fun ChatScreen(viewModel: ChatViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val level by viewModel.voiceLevel.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val listState = rememberLazyListState()

    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) viewModel.toggleLive() else viewModel.clearError()
    }

    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.lastIndex)
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(PitchBlack)
            .padding(horizontal = 16.dp)
    ) {
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "Live Chat",
                fontSize = 30.sp,
                color = OffWhite,
                fontWeight = FontWeight.Light,
                modifier = Modifier.weight(1f),
            )
            Text(
                text = if (state.liveActive) "Live" else "Text or voice",
                fontSize = 12.sp,
                color = if (state.liveActive) AgentOrangeBubble else MutedWarmGrey,
                fontWeight = FontWeight.SemiBold,
            )
        }
        Spacer(Modifier.height(10.dp))

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val chipColors = AssistChipDefaults.assistChipColors(
                containerColor = Color(0x18FFFFFF),
                labelColor = OffWhite,
            )
            val chipBorder = AssistChipDefaults.assistChipBorder(
                enabled = true,
                borderColor = Color(0x3DFFFFFF),
            )
            AssistChip(
                onClick = { viewModel.setDraft("What is on my screen right now?"); viewModel.send() },
                label = { Text("On screen", fontSize = 12.sp) },
                shape = RoundedCornerShape(999.dp),
                colors = chipColors,
                border = chipBorder,
            )
            AssistChip(
                onClick = { viewModel.setDraft("How much time have I used today?"); viewModel.send() },
                label = { Text("Screen time", fontSize = 12.sp) },
                shape = RoundedCornerShape(999.dp),
                colors = chipColors,
                border = chipBorder,
            )
            AssistChip(
                onClick = { viewModel.setDraft("Open the Settings app on my phone"); viewModel.send() },
                label = { Text("Settings", fontSize = 12.sp) },
                shape = RoundedCornerShape(999.dp),
                colors = chipColors,
                border = chipBorder,
            )
        }
        Spacer(Modifier.height(12.dp))

        LazyColumn(
            state = listState,
            modifier = Modifier.weight(1f),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (state.messages.isEmpty() && state.livePartial.isBlank()) {
                item {
                    val introShape = RoundedCornerShape(26.dp)
                    Surface(
                        color = Color.Transparent,
                        shape = introShape,
                        border = BorderStroke(1.dp, WarmGlassRim),
                        modifier = Modifier
                            .fillMaxWidth(0.90f)
                            .shadow(12.dp, introShape)
                            .clip(introShape)
                            .background(OrangeBubbleBrush),
                    ) {
                        Column(modifier = Modifier.padding(horizontal = 18.dp, vertical = 16.dp)) {
                            Text(
                                text = "OYI",
                                color = OrangeMetaInk,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                text = "Speak naturally or type a task for your phone. You can interrupt me mid-sentence anytime.",
                                color = DarkInk,
                                fontSize = 14.5.sp,
                                fontWeight = FontWeight.Medium,
                                lineHeight = 20.sp,
                            )
                        }
                    }
                }
            }
            items(state.messages, key = { it.id }) { message ->
                MessageBubble(message)
            }
            if (state.livePartial.isNotBlank()) {
                item {
                    MessageBubble(ChatMessage(role = ChatRole.USER, text = state.livePartial, pending = true))
                }
            }
        }

        if (state.error.isNotBlank()) {
            val errShape = RoundedCornerShape(18.dp)
            Surface(
                color = Color.Transparent,
                shape = errShape,
                border = BorderStroke(1.dp, WarmGlassRim),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(vertical = 4.dp)
                    .clip(errShape)
                    .background(OrangeBubbleBrush),
            ) {
                Text(
                    text = state.error,
                    style = MaterialTheme.typography.labelMedium,
                    color = DarkInk,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                )
            }
        }

        // Liquid Glass floating pill composer bar
        val composerShape = RoundedCornerShape(999.dp)
        Surface(
            color = Color.Transparent,
            shape = composerShape,
            border = BorderStroke(1.dp, DarkGlassRim),
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = 10.dp)
                .shadow(14.dp, composerShape)
                .clip(composerShape)
                .background(SmokedGlassBrush),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 8.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = state.draft,
                    onValueChange = viewModel::setDraft,
                    placeholder = { Text("Message OYI…", color = MutedWarmGrey, fontSize = 14.sp) },
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(999.dp),
                    maxLines = 3,
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = Color.Transparent,
                        unfocusedBorderColor = Color.Transparent,
                        focusedTextColor = OffWhite,
                        unfocusedTextColor = OffWhite,
                        cursorColor = AgentOrangeBubble,
                    ),
                )
                Spacer(Modifier.width(6.dp))
                MicButton(
                    active = state.liveActive,
                    level = level,
                    onClick = {
                        if (state.liveActive) {
                            viewModel.toggleLive()
                        } else if (ContextCompat.checkSelfPermission(
                                context,
                                Manifest.permission.RECORD_AUDIO,
                            ) == PackageManager.PERMISSION_GRANTED
                        ) {
                            viewModel.toggleLive()
                        } else {
                            micPermission.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    },
                )
                Spacer(Modifier.width(6.dp))
                val canSend = state.draft.isNotBlank() && !state.sending
                Box(
                    modifier = Modifier
                        .size(44.dp)
                        .clip(CircleShape)
                        .background(if (canSend) UserCreamBubble else Color(0x1FFFFFFF))
                        .clickable(enabled = canSend, onClick = viewModel::send),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(
                        painter = painterResource(R.drawable.ic_island_send),
                        contentDescription = "Send",
                        tint = if (canSend) DarkInk else MutedWarmGrey,
                        modifier = Modifier.size(19.dp),
                    )
                }
            }
        }
    }
}

/** The live-voice switch. While on, a ring breathes with your voice. */
@Composable
private fun MicButton(active: Boolean, level: Float, onClick: () -> Unit) {
    val ring by animateFloatAsState(
        targetValue = if (active) 1f + level.coerceIn(0f, 1f) * 0.45f else 1f,
        animationSpec = spring(dampingRatio = 0.6f, stiffness = Spring.StiffnessMedium),
        label = "micRing",
    )
    Box(modifier = Modifier.size(44.dp), contentAlignment = Alignment.Center) {
        if (active) {
            Box(
                modifier = Modifier
                    .size(44.dp)
                    .scale(ring)
                    .clip(CircleShape)
                    .background(AgentOrangeBubble.copy(alpha = 0.28f)),
            )
        }
        Box(
            modifier = Modifier
                .size(44.dp)
                .clip(CircleShape)
                .background(if (active) AgentOrangeBubble else Color(0x2EFFFFFF))
                .clickable(onClick = onClick),
            contentAlignment = Alignment.Center,
        ) {
            if (active) {
                Box(
                    Modifier
                        .size(13.dp)
                        .clip(RoundedCornerShape(3.dp))
                        .background(DarkInk),
                )
            } else {
                Icon(
                    painter = painterResource(R.drawable.ic_island_mic),
                    contentDescription = "Start live voice",
                    tint = OffWhite,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val mine = message.role == ChatRole.USER
    val bgBrush = when (message.role) {
        ChatRole.USER -> CreamBubbleBrush
        ChatRole.AGENT -> OrangeBubbleBrush
        ChatRole.SYSTEM -> SmokedGlassBrush
    }
    val rimBrush = if (message.role == ChatRole.SYSTEM) DarkGlassRim else WarmGlassRim
    val textColor = if (message.role == ChatRole.SYSTEM) OffWhite else DarkInk
    val metaColor = when (message.role) {
        ChatRole.USER -> CreamMetaInk
        ChatRole.AGENT -> OrangeMetaInk
        ChatRole.SYSTEM -> MutedWarmGrey
    }
    val roleLabel = when (message.role) {
        ChatRole.USER -> if (message.pending) "You · speaking" else "You"
        ChatRole.AGENT -> if (message.pending) "OYI · typing" else "OYI"
        ChatRole.SYSTEM -> "Notice"
    }
    val align: Alignment = if (mine) Alignment.CenterEnd else Alignment.CenterStart
    val bubbleShape = RoundedCornerShape(
        topStart = 26.dp,
        topEnd = 26.dp,
        bottomStart = if (mine) 26.dp else 8.dp,
        bottomEnd = if (mine) 8.dp else 26.dp,
    )
    Box(modifier = Modifier.fillMaxWidth(), contentAlignment = align) {
        Surface(
            color = Color.Transparent,
            shape = bubbleShape,
            border = BorderStroke(1.dp, rimBrush),
            modifier = Modifier
                .fillMaxWidth(0.86f)
                .shadow(10.dp, bubbleShape)
                .clip(bubbleShape)
                .background(bgBrush),
        ) {
            Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 13.dp)) {
                Text(
                    text = roleLabel,
                    color = metaColor,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                )
                Spacer(Modifier.height(3.dp))
                Text(
                    text = message.text,
                    fontSize = 14.5.sp,
                    fontWeight = FontWeight.Medium,
                    lineHeight = 20.sp,
                    color = textColor,
                )
            }
        }
    }
}
