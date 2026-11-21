package ai.oyi.device.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import ai.oyi.device.OyiApplication
import ai.oyi.device.net.ChatClient
import ai.oyi.device.voice.LiveVoiceController
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

enum class ChatRole { USER, AGENT, SYSTEM }

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val role: ChatRole,
    val text: String,
    val pending: Boolean = false,
)

data class ChatState(
    val messages: List<ChatMessage> = emptyList(),
    val draft: String = "",
    val sending: Boolean = false,
    val liveActive: Boolean = false,
    val livePartial: String = "",
    val liveStatus: String = "",
    val error: String = "",
)

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    private val container = (app as OyiApplication).container
    private val chat = ChatClient(container.settings)
    private val voice = LiveVoiceController(app, chat)

    /** Stable per-install conversation id so the agent keeps context. */
    private val threadId = "android-${container.settings.settings.value.deviceId.take(8)}"

    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state.asStateFlow()

    val voiceEvents = voice.events

    /** Microphone level for the mic button's ring. */
    val voiceLevel: StateFlow<Float> = container.runtime.voiceLevel

    /** Everything a turn does also shows on the Dynamic Island. */
    private val island = container.runtime

    /** A spoken reply is playing (or about to): "Done" waits for it. */
    private var replyAudioPending = false
    private var turnEndedWhileSpeaking = false

    private val interruptReply: () -> Unit = { voice.interrupt() }

    init {
        // A double tap on the island cuts the spoken reply short.
        island.onInterruptAgent = interruptReply
        viewModelScope.launch {
            voice.events.collect { event ->
                when (event) {
                    is LiveVoiceController.Event.Ready -> {
                        _state.update {
                            it.copy(liveStatus = "Listening…", livePartial = "")
                        }
                        island.agentListening()
                    }
                    is LiveVoiceController.Event.Partial -> {
                        _state.update { it.copy(livePartial = event.text) }
                        if (event.text.isNotBlank() && !replyAudioPending) {
                            island.agentListening("Hearing you…")
                        }
                    }
                    is LiveVoiceController.Event.Tool ->
                        island.agentTool(event.label, event.done)
                    is LiveVoiceController.Event.UserText -> {
                        _state.update {
                            it.copy(
                                messages = it.messages + ChatMessage(role = ChatRole.USER, text = event.text),
                                livePartial = "",
                            )
                        }
                    }
                    is LiveVoiceController.Event.BotText -> {
                        appendAgentText(event.text)
                        if (!replyAudioPending) island.agentReplying()
                    }
                    is LiveVoiceController.Event.BotAudio -> {
                        replyAudioPending = true
                        val last = _state.value.messages.lastOrNull()
                        if (last == null || last.role != ChatRole.AGENT || !last.pending) {
                            appendAgentText(event.text)
                        }
                        voice.playAudio(event.audio)
                    }
                    is LiveVoiceController.Event.Filler -> {
                        if (event.url.isNotBlank()) voice.playClip(event.url)
                    }
                    is LiveVoiceController.Event.Status -> {
                        _state.update { it.copy(liveStatus = event.text) }
                        onVoiceStatus(event.text)
                    }
                    is LiveVoiceController.Event.Failed -> {
                        _state.update { it.copy(error = event.message, liveStatus = "") }
                        replyAudioPending = false
                        turnEndedWhileSpeaking = false
                        island.agentError(event.message)
                    }
                    is LiveVoiceController.Event.TurnEnded -> {
                        if (replyAudioPending) {
                            turnEndedWhileSpeaking = true
                        } else {
                            island.agentTurnDone(listening = voice.active.value)
                        }
                        _state.update { current ->
                            current.copy(
                                liveStatus = "Listening…",
                                messages = current.messages.map { msg ->
                                    if (msg.role == ChatRole.AGENT && msg.pending) {
                                        msg.copy(pending = false)
                                    } else {
                                        msg
                                    }
                                },
                            )
                        }
                    }
                }
            }
        }
        viewModelScope.launch {
            voice.active.collect { active ->
                _state.update { it.copy(liveActive = active) }
                if (!active) {
                    replyAudioPending = false
                    turnEndedWhileSpeaking = false
                    island.setVoiceLevel(0f)
                    if (!_state.value.sending) island.agentRest(listening = false)
                }
            }
        }
        viewModelScope.launch {
            voice.level.collect { level -> island.setVoiceLevel(level) }
        }
    }

    /** Maps live-voice captions to island states. */
    private fun onVoiceStatus(text: String) {
        val listening = voice.active.value
        when {
            text.startsWith("Thinking") -> island.agentThinking(text)
            text.startsWith("Speaking") -> island.agentSpeaking()
            text.startsWith("Listening") || text.startsWith("Go ahead") -> {
                // Playback finished or was cut off.
                val finished = turnEndedWhileSpeaking
                replyAudioPending = false
                turnEndedWhileSpeaking = false
                if (finished) island.agentTurnDone(listening) else island.agentListening(text.trimEnd('…'))
            }
            else -> if (!replyAudioPending) island.agentListening(text)
        }
    }

    fun setDraft(text: String) {
        _state.update { it.copy(draft = text) }
    }

    /** Sends the typed message to the agent and streams the reply in. */
    fun send() {
        val text = _state.value.draft.trim()
        if (text.isEmpty() || _state.value.sending) return
        _state.update {
            it.copy(
                draft = "",
                sending = true,
                messages = it.messages + ChatMessage(role = ChatRole.USER, text = text),
                error = "",
            )
        }
        island.agentThinking()
        viewModelScope.launch {
            var answered = false
            try {
                chat.stream(text, threadId).collect { event ->
                    when (event) {
                        is ChatClient.StreamEvent.Tool -> island.agentTool(event.label, event.done)
                        is ChatClient.StreamEvent.Text -> {
                            answered = true
                            appendAgentText(event.text)
                            island.agentReplying()
                        }
                    }
                }
            } catch (error: Exception) {
                island.agentError(error.message ?: "Could not reach OYI")
                _state.update {
                    it.copy(
                        sending = false,
                        error = error.message ?: "Chat failed",
                        messages = it.messages + ChatMessage(
                            role = ChatRole.SYSTEM,
                            text = "Could not reach OYI: ${error.message ?: "unknown error"}",
                        ),
                    )
                }
                return@launch
            }
            _state.update { current ->
                val messages = if (answered) {
                    current.messages.map { message ->
                        if (message.role == ChatRole.AGENT && message.pending) {
                            message.copy(pending = false)
                        } else {
                            message
                        }
                    }
                } else {
                    current.messages + ChatMessage(
                        role = ChatRole.SYSTEM,
                        text = "OYI did not answer.",
                    )
                }
                current.copy(sending = false, messages = messages)
            }
            island.agentTurnDone(listening = voice.active.value)
        }
    }

    /** Starts or stops the live microphone. */
    fun toggleLive() {
        if (_state.value.liveActive) {
            voice.stop()
            _state.update { it.copy(liveStatus = "", livePartial = "") }
            island.agentRest(listening = false)
        } else {
            voice.start(threadId)
            _state.update { it.copy(liveStatus = "Connecting…", error = "") }
        }
    }

    fun clearError() {
        _state.update { it.copy(error = "") }
    }

    private fun appendAgentText(delta: String) {
        if (delta.isBlank()) return
        _state.update { current ->
            val last = current.messages.lastOrNull()
            val messages = if (last != null && last.role == ChatRole.AGENT && last.pending) {
                // Keep the bubble pending while the reply is still streaming.
                current.messages.dropLast(1) + last.copy(text = last.text + delta)
            } else {
                current.messages + ChatMessage(role = ChatRole.AGENT, text = delta, pending = true)
            }
            current.copy(messages = messages)
        }
    }

    override fun onCleared() {
        if (island.onInterruptAgent === interruptReply) island.onInterruptAgent = null
        super.onCleared()
    }
}
