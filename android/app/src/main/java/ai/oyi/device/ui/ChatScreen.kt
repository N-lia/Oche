package ai.oyi.device.ui

import ai.oyi.device.R
import ai.oyi.device.ui.theme.MorphingLoader
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.PressMorph
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

/** Everything the chat screen can ask for. The defaults do nothing, for previews. */
class ChatActions(
    val setDraft: (String) -> Unit = {},
    val send: () -> Unit = {},
    /** The mic button: starts or stops live voice (asking for the mic first). */
    val toggleMic: () -> Unit = {},
    /** Takes the person to Space to link a gateway. */
    val openSetup: () -> Unit = {},
)

private class Suggestion(val text: String, val prompt: String, @DrawableRes val icon: Int)

private val Suggestions = listOf(
    Suggestion("What's on my screen?", "What is on my screen right now?", R.drawable.ic_visibility),
    Suggestion("My screen time today", "How much time have I used today?", R.drawable.ic_timer),
    Suggestion("Open Settings for me", "Open the Settings app on my phone", R.drawable.ic_smartphone),
)

@Composable
fun ChatScreen(viewModel: ChatViewModel = viewModel(), onOpenSetup: () -> Unit = {}) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val level by viewModel.voiceLevel.collectAsStateWithLifecycle()
    val needsSetup by viewModel.needsSetup.collectAsStateWithLifecycle()
    val context = LocalContext.current

    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) viewModel.toggleLive() else viewModel.clearError()
    }
    val actions = remember(viewModel, onOpenSetup) {
        ChatActions(
            setDraft = viewModel::setDraft,
            send = viewModel::send,
            toggleMic = {
                if (viewModel.state.value.liveActive ||
                    ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) ==
                    PackageManager.PERMISSION_GRANTED
                ) {
                    viewModel.toggleLive()
                } else {
                    micPermission.launch(Manifest.permission.RECORD_AUDIO)
                }
            },
            openSetup = onOpenSetup,
        )
    }
    ChatContent(state, level, actions, needsSetup)
}

/** One row of the conversation, with repeats of the same notice folded together. */
private class ChatRow(val message: ChatMessage, val repeats: Int, val firstOfRun: Boolean)

private fun foldRows(messages: List<ChatMessage>): List<ChatRow> {
    val rows = mutableListOf<ChatRow>()
    messages.forEach { message ->
        val last = rows.lastOrNull()
        if (last != null && message.role == ChatRole.SYSTEM &&
            last.message.role == ChatRole.SYSTEM && last.message.text == message.text
        ) {
            rows[rows.lastIndex] = ChatRow(message, last.repeats + 1, last.firstOfRun)
        } else {
            rows += ChatRow(message, 1, firstOfRun = last?.message?.role != message.role)
        }
    }
    return rows
}

/** The chat screen itself, without a view model (previews and snapshots use it). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChatContent(state: ChatState, level: Float, actions: ChatActions, needsSetup: Boolean = false) {
    val listState = rememberLazyListState()
    val rows = remember(state.messages) { foldRows(state.messages) }
    val empty = state.messages.isEmpty() && state.livePartial.isBlank()

    LaunchedEffect(rows.size, state.livePartial.isNotBlank()) {
        val count = listState.layoutInfo.totalItemsCount
        if (count > 0) listState.animateScrollToItem(count - 1)
    }

    // The error bar only says what the conversation does not already say.
    val error = state.error.takeIf { err ->
        err.isNotBlank() && state.messages.none { it.role == ChatRole.SYSTEM && it.text.contains(err) }
    }.orEmpty()

    val imeOpen = WindowInsets.isImeVisible
    Column(
        modifier = Modifier
            .fillMaxSize()
            .imePadding()
            .then(
                if (imeOpen) {
                    Modifier.padding(bottom = 8.dp)
                } else {
                    Modifier.navigationBarsPadding().padding(bottom = ComposerLift)
                },
            ),
    ) {
        Box(Modifier.padding(horizontal = Gutter)) {
            ScreenTitle(
                title = "Chat",
                subtitle = when {
                    needsSetup -> "Not linked yet"
                    state.liveActive -> state.liveStatus.ifBlank { "Listening" }
                    else -> "Type, or tap the mic and talk"
                },
                trailing = {
                    if (state.liveActive) StatusLabel("Live", Oyi.Orange)
                },
            )
        }

        when {
            needsSetup -> SetupState(actions.openSetup, Modifier.weight(1f))
            empty -> EmptyState(actions, Modifier.weight(1f))
            else -> LazyColumn(
                state = listState,
                modifier = Modifier.weight(1f),
                contentPadding = PaddingValues(horizontal = Gutter, vertical = 8.dp),
            ) {
                items(rows, key = { it.message.id }) { row ->
                    Box(Modifier.padding(top = if (row.firstOfRun) 14.dp else 4.dp)) {
                        MessageRow(row)
                    }
                }
                if (state.livePartial.isNotBlank()) {
                    item(key = "partial") {
                        Box(Modifier.padding(top = 14.dp)) {
                            MessageRow(
                                ChatRow(ChatMessage(role = ChatRole.USER, text = state.livePartial, pending = true), 1, true),
                            )
                        }
                    }
                }
            }
        }

        if (error.isNotBlank()) {
            val shape = RoundedCornerShape(16.dp)
            Row(
                modifier = Modifier
                    .padding(horizontal = Gutter, vertical = 6.dp)
                    .fillMaxWidth()
                    .clip(shape)
                    .background(Oyi.Raised)
                    .border(1.dp, Oyi.Hairline, shape)
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(painterResource(R.drawable.ic_cloud_off), null, tint = Oyi.Bad, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(10.dp))
                Text(error, color = Oyi.Muted, fontSize = 13.sp, lineHeight = 18.sp, maxLines = 2)
            }
        }

        if (!needsSetup) {
            Composer(state, level, actions)
        }
    }
}

/** How far the composer sits above the bottom: clear of the floating nav. */
private val ComposerLift = NavHeight + NavBottomMargin + 10.dp

@Composable
private fun SetupState(onSetup: () -> Unit, modifier: Modifier) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Gutter),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Image(painterResource(R.drawable.oyi_face_sleeping), contentDescription = null, modifier = Modifier.size(104.dp))
        Spacer(Modifier.height(20.dp))
        Text(
            "Link OYI to start chatting",
            style = MaterialTheme.typography.headlineSmall,
            color = Oyi.OffWhite,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "Add your gateway address and pairing token in Space. It takes a minute.",
            color = Oyi.Muted,
            fontSize = 14.sp,
            lineHeight = 20.sp,
            textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(max = 300.dp),
        )
        Spacer(Modifier.height(24.dp))
        OyiButton(text = "Set up in Space", onClick = onSetup, icon = R.drawable.ic_link)
        Spacer(Modifier.height(48.dp))
    }
}

@Composable
private fun EmptyState(actions: ChatActions, modifier: Modifier) {
    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = Gutter),
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().weight(1f),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Image(painterResource(R.drawable.oyi_face_idea), contentDescription = null, modifier = Modifier.size(96.dp))
            Spacer(Modifier.height(16.dp))
            Text("What can I do for you?", style = MaterialTheme.typography.headlineSmall, color = Oyi.OffWhite)
            Spacer(Modifier.height(6.dp))
            Text(
                "Ask anything, or have OYI work your phone. You can cut in while it talks.",
                color = Oyi.Muted,
                fontSize = 14.sp,
                lineHeight = 20.sp,
                textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(max = 300.dp),
            )
        }
        OyiCard(padding = PaddingValues(horizontal = 14.dp, vertical = 4.dp)) {
            Suggestions.forEachIndexed { index, suggestion ->
                if (index > 0) RowDivider(inset = 50.dp)
                ListRow(
                    title = suggestion.text,
                    leading = { IconTile(suggestion.icon, size = 36.dp) },
                    onClick = {
                        actions.setDraft(suggestion.prompt)
                        actions.send()
                    },
                ) {
                    Icon(painterResource(R.drawable.ic_arrow_upward), null, tint = Oyi.Faint, modifier = Modifier.size(18.dp))
                }
            }
        }
        Spacer(Modifier.height(10.dp))
    }
}

@Composable
private fun MessageRow(row: ChatRow) {
    val message = row.message
    when (message.role) {
        ChatRole.SYSTEM -> SystemNotice(message.text, row.repeats)
        ChatRole.USER -> Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            Bubble(
                text = message.text,
                background = if (message.pending) Oyi.Cream.copy(alpha = 0.55f) else Oyi.Cream,
                textColor = Oyi.Ink,
                shape = RoundedCornerShape(
                    topStart = 22.dp,
                    topEnd = if (row.firstOfRun) 22.dp else 8.dp,
                    bottomStart = 22.dp,
                    bottomEnd = 8.dp,
                ),
                modifier = Modifier.padding(start = 56.dp),
            )
        }
        ChatRole.AGENT -> Row(Modifier.fillMaxWidth().padding(end = 40.dp), verticalAlignment = Alignment.Top) {
            if (row.firstOfRun) {
                Image(
                    painterResource(if (message.pending) R.drawable.oyi_face_talking else R.drawable.oyi_face_neutral),
                    contentDescription = "OYI",
                    modifier = Modifier.size(30.dp),
                )
            } else {
                Spacer(Modifier.width(30.dp))
            }
            Spacer(Modifier.width(8.dp))
            if (message.pending && message.text.isBlank()) {
                Box(
                    Modifier
                        .clip(RoundedCornerShape(22.dp))
                        .background(Oyi.Raised)
                        .padding(horizontal = 18.dp, vertical = 14.dp),
                ) {
                    MorphingLoader(color = Oyi.Muted, size = 18.dp)
                }
            } else {
                Bubble(
                    text = message.text,
                    background = Oyi.Raised,
                    textColor = Oyi.OffWhite,
                    shape = RoundedCornerShape(
                        topStart = if (row.firstOfRun) 8.dp else 22.dp,
                        topEnd = 22.dp,
                        bottomStart = 22.dp,
                        bottomEnd = 22.dp,
                    ),
                    bordered = true,
                )
            }
        }
    }
}

@Composable
private fun Bubble(
    text: String,
    background: Color,
    textColor: Color,
    shape: RoundedCornerShape,
    modifier: Modifier = Modifier,
    bordered: Boolean = false,
) {
    Text(
        text = text,
        color = textColor,
        fontSize = 15.sp,
        lineHeight = 21.sp,
        modifier = modifier
            .clip(shape)
            .background(background)
            .then(if (bordered) Modifier.border(1.dp, Oyi.Hairline, shape) else Modifier)
            .padding(horizontal = 16.dp, vertical = 11.dp),
    )
}

/** A quiet centred line: the conversation's own small print. */
@Composable
private fun SystemNotice(text: String, repeats: Int) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp, horizontal = 24.dp),
        horizontalArrangement = Arrangement.Center,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = text,
            color = Oyi.Faint,
            fontSize = 12.5.sp,
            lineHeight = 17.sp,
            textAlign = TextAlign.Center,
            modifier = Modifier.weight(1f, fill = false),
        )
        if (repeats > 1) {
            Spacer(Modifier.width(6.dp))
            Text(
                "×$repeats",
                color = Oyi.Muted,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                modifier = Modifier
                    .clip(Oyi.Pill)
                    .background(Oyi.Raised)
                    .padding(horizontal = 7.dp, vertical = 2.dp),
            )
        }
    }
}

@Composable
private fun Composer(state: ChatState, level: Float, actions: ChatActions) {
    val canSend = state.draft.isNotBlank() && !state.sending
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(start = Gutter - 4.dp, end = Gutter - 4.dp, top = 6.dp),
        verticalAlignment = Alignment.Bottom,
    ) {
        BasicTextField(
            value = state.draft,
            onValueChange = actions.setDraft,
            maxLines = 4,
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = Oyi.OffWhite),
            cursorBrush = SolidColor(Oyi.Orange),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            modifier = Modifier.weight(1f),
            decorationBox = { inner ->
                val shape = RoundedCornerShape(24.dp)
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 48.dp)
                        .clip(shape)
                        .background(Oyi.Raised)
                        .border(1.dp, Oyi.Hairline, shape)
                        .padding(horizontal = 18.dp, vertical = 13.dp),
                    contentAlignment = Alignment.CenterStart,
                ) {
                    if (state.draft.isEmpty()) {
                        Text(
                            if (state.liveActive) "Listening, or type" else "Message OYI",
                            color = Oyi.Faint,
                            style = MaterialTheme.typography.bodyLarge,
                        )
                    }
                    inner()
                }
            },
        )
        Spacer(Modifier.width(8.dp))
        // One button that changes job: send when there is text, mic otherwise.
        if (canSend || state.sending) {
            SendButton(enabled = canSend, sending = state.sending, onClick = actions.send)
        } else {
            MicButton(active = state.liveActive, level = level, onClick = actions.toggleMic)
        }
    }
}

@Composable
private fun SendButton(enabled: Boolean, sending: Boolean, onClick: () -> Unit) {
    PressMorph(
        onClick = onClick,
        enabled = enabled,
        modifier = Modifier.size(48.dp),
        pressedPercent = 30f,
    ) { shape ->
        Box(
            Modifier
                .matchParentSize()
                .background(if (enabled) Oyi.Orange else Oyi.Control, shape)
                .semantics { contentDescription = "Send" },
            contentAlignment = Alignment.Center,
        ) {
            if (sending) {
                MorphingLoader(color = Oyi.Muted, size = 20.dp)
            } else {
                Icon(painterResource(R.drawable.ic_arrow_upward), null, tint = Oyi.Ink, modifier = Modifier.size(22.dp))
            }
        }
    }
}

/** The live-voice switch. While on, a ring breathes with your voice. */
@Composable
private fun MicButton(active: Boolean, level: Float, onClick: () -> Unit) {
    val ring by animateFloatAsState(
        targetValue = if (active) 1f + level.coerceIn(0f, 1f) * 0.22f else 1f,
        animationSpec = OyiMotion.fastSpatial(),
        label = "micRing",
    )
    // Expressive toggle: the round button squares off while live.
    val corner by animateFloatAsState(
        targetValue = if (active) 30f else 50f,
        animationSpec = OyiMotion.fastSpatial(),
        label = "micCorner",
    )
    val bg by animateColorAsState(if (active) Oyi.Orange else Oyi.Raised, OyiMotion.defaultEffects(), label = "micBg")
    Box(modifier = Modifier.size(48.dp), contentAlignment = Alignment.Center) {
        if (active) {
            Box(
                modifier = Modifier
                    .size(48.dp)
                    .scale(ring)
                    .clip(CircleShape)
                    .background(Oyi.Orange.copy(alpha = 0.22f)),
            )
        }
        PressMorph(
            onClick = onClick,
            restPercent = corner,
            pressedPercent = 24f,
            modifier = Modifier.size(48.dp),
        ) { shape ->
            Box(
                Modifier
                    .matchParentSize()
                    .background(bg, shape)
                    .then(if (active) Modifier else Modifier.border(1.dp, Oyi.Hairline, shape))
                    .semantics { contentDescription = if (active) "Stop live voice" else "Start live voice" },
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    painter = painterResource(if (active) R.drawable.ic_stop else R.drawable.ic_mic),
                    contentDescription = null,
                    tint = if (active) Oyi.Ink else Oyi.OffWhite,
                    modifier = Modifier.size(22.dp),
                )
            }
        }
    }
}
