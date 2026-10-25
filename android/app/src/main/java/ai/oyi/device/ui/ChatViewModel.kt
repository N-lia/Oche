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

    init {
        viewModelScope.launch {
            voice.events.collect { event ->
                when (event) {
                    is LiveVoiceController.Event.Ready -> {
                        _state.update {
                            it.copy(liveStatus = "Listening…", livePartial = "")
                        }
                    }
                    is LiveVoiceController.Event.Partial ->
                        _state.update { it.copy(livePartial = event.text) }
                    is LiveVoiceController.Event.UserText -> {
                        _state.update {
                            it.copy(
                                messages = it.messages + ChatMessage(role = ChatRole.USER, text = event.text),
                                livePartial = "",
                            )
                        }
                    }
                    is LiveVoiceController.Event.BotText ->
                        appendAgentText(event.text)
                    is LiveVoiceController.Event.BotAudio -> {
                        appendAgentText(event.text)
                        voice.playAudio(event.audio)
                    }
                    is LiveVoiceController.Event.Filler -> {
                        if (event.url.isNotBlank()) voice.playClip(event.url)
                    }
                    is LiveVoiceController.Event.Failed ->
                        _state.update { it.copy(error = event.message, liveStatus = "") }
                    is LiveVoiceController.Event.TurnEnded ->
                        _state.update { it.copy(liveStatus = "Listening…") }
                }
            }
        }
        viewModelScope.launch {
            voice.active.collect { active ->
                _state.update { it.copy(liveActive = active) }
            }
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
        viewModelScope.launch {
            var answered = false
            try {
                chat.stream(text, threadId).collect { delta ->
                    answered = true
                    appendAgentText(delta)
                }
            } catch (error: Exception) {
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
        }
    }

    /** Starts or stops the live microphone. */
    fun toggleLive() {
        if (_state.value.liveActive) {
            voice.stop()
            _state.update { it.copy(liveStatus = "", livePartial = "") }
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
}
