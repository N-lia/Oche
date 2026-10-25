package ai.oyi.device.voice

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.util.Log
import ai.oyi.device.net.ChatClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/**
 * Live voice chat with the OYI agent.
 *
 * Mirrors the browser live-chat client:
 *   - binary frames  -> raw 16 kHz mono PCM16 microphone audio
 *   - text frames    -> control messages in, server events out
 *   - "bot_audio"    -> base64 MP3 that is played back locally
 */
class LiveVoiceController(
    private val context: Context,
    private val chat: ChatClient,
) {

    sealed interface Event {
        data class Ready(val sampleRate: Int, val smartTurn: Boolean) : Event
        data class Partial(val text: String) : Event
        data class UserText(val text: String) : Event
        data class BotText(val text: String) : Event
        data class BotAudio(val text: String, val audio: ByteArray) : Event
        data class Filler(val phrase: String, val url: String) : Event
        data class Failed(val message: String) : Event
        data object TurnEnded : Event
    }

    private val _events = MutableSharedFlow<Event>(extraBufferCapacity = 64)
    val events: SharedFlow<Event> = _events.asSharedFlow()

    private val _active = MutableStateFlow(false)
    val active: StateFlow<Boolean> = _active.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var socket: WebSocket? = null
    private var recorder: AudioRecord? = null
    private var recordJob: Job? = null
    private var player: MediaPlayer? = null

    /** Opens the voice socket and starts streaming microphone audio. */
    @SuppressLint("MissingPermission")
    fun start(threadId: String) {
        if (_active.value) return
        val url = chat.voiceUrl()
        if (url == null) {
            scope.launch { _events.emit(Event.Failed("Set the OYI gateway URL first")) }
            return
        }

        val minBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuffer <= 0) {
            scope.launch { _events.emit(Event.Failed("Microphone is not available")) }
            return
        }

        val request = Request.Builder().url(url).build()
        socket = http.newWebSocket(request, listener)

        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuffer * 2,
            )
        } catch (error: Exception) {
            scope.launch { _events.emit(Event.Failed("Cannot open the microphone: ${error.message}")) }
            return
        }

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            scope.launch { _events.emit(Event.Failed("Cannot open the microphone")) }
            return
        }

        recorder = record
        record.startRecording()
        _active.value = true

        recordJob = scope.launch(Dispatchers.IO) {
            val buffer = ByteArray(minBuffer)
            while (true) {
                val read = runCatching { record.read(buffer, 0, buffer.size) }.getOrDefault(-1)
                if (read <= 0) continue
                socket?.send(buffer.toByteString(0, read))
            }
        }

        // Handshake: the server replies with "ready".
        val control = JSONObject().apply {
            put("type", "start")
            put("sample_rate", SAMPLE_RATE)
            put("mode", "agent")
            put("mood", "normal")
            put("thread_id", threadId)
        }
        socket?.send(control.toString())
    }

    /** Stops the microphone and closes the socket. */
    fun stop() {
        _active.value = false
        recordJob?.cancel()
        recordJob = null
        runCatching { recorder?.stop() }
        runCatching { recorder?.release() }
        recorder = null
        socket?.send(JSONObject().apply { put("type", "stop") }.toString())
        socket?.close(1000, "stopped")
        socket = null
        stopPlayback()
        scope.cancel()
    }

    /** Plays a filler/backchannel clip (an MP3 URL). */
    fun playClip(url: String) {
        scope.launch {
            withContext(Dispatchers.Main) {
                runCatching {
                    stopPlayback()
                    player = MediaPlayer().apply {
                        setDataSource(url)
                        setOnPreparedListener { it.start() }
                        setOnCompletionListener { stopPlayback() }
                        prepareAsync()
                    }
                }.onFailure { Log.w(TAG, "Clip playback failed: ${it.message}") }
            }
        }
    }

    /** Plays the agent's reply (a base64 MP3 payload). */
    fun playAudio(bytes: ByteArray) {
        scope.launch {
            withContext(Dispatchers.Main) {
                runCatching {
                    stopPlayback()
                    val file = File(context.cacheDir, "oyi_reply_${System.currentTimeMillis()}.mp3")
                    file.writeBytes(bytes)
                    player = MediaPlayer().apply {
                        setDataSource(file.absolutePath)
                        setOnPreparedListener { it.start() }
                        setOnCompletionListener {
                            stopPlayback()
                            runCatching { file.delete() }
                        }
                        prepareAsync()
                    }
                }.onFailure { Log.w(TAG, "Reply playback failed: ${it.message}") }
            }
        }
    }

    private fun stopPlayback() {
        runCatching { player?.stop() }
        runCatching { player?.release() }
        player = null
    }

    private val listener = object : WebSocketListener() {

        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "voice socket open")
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val payload = runCatching { JSONObject(text) }.getOrNull() ?: return
            when (payload.optString("type")) {
                "ready" -> emit(
                    Event.Ready(
                        sampleRate = payload.optInt("sample_rate", SAMPLE_RATE),
                        smartTurn = payload.optBoolean("smart_turn", false),
                    )
                )
                "partial" -> emit(Event.Partial(payload.optString("text")))
                "user_text" -> emit(Event.UserText(payload.optString("text")))
                "bot_text_delta" -> emit(Event.BotText(payload.optString("text")))
                "bot_audio" -> {
                    val audio = runCatching {
                        android.util.Base64.decode(payload.optString("audio"), android.util.Base64.DEFAULT)
                    }.getOrNull()
                    if (audio != null) {
                        emit(Event.BotAudio(payload.optString("text"), audio))
                    }
                }
                "filler" -> emit(Event.Filler(payload.optString("phrase"), payload.optString("url")))
                "error" -> emit(Event.Failed(payload.optString("message", "Voice error")))
                "turn_end" -> emit(Event.TurnEnded)
                "interrupted" -> stopPlayback()
            }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) = Unit

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            Log.w(TAG, "voice socket failed: ${t.message}")
            emit(Event.Failed(t.message ?: "Voice connection failed"))
            _active.value = false
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            Log.i(TAG, "voice socket closed: $code $reason")
            _active.value = false
        }
    }

    private fun emit(event: Event) {
        scope.launch { _events.emit(event) }
    }

    companion object {
        private const val TAG = "OyiVoice"
        const val SAMPLE_RATE = 16_000
    }
}
