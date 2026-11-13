package ai.oyi.device.voice

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.NoiseSuppressor
import android.os.SystemClock
import android.util.Base64
import android.util.Log
import ai.oyi.device.net.ChatClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
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
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/**
 * Live voice chat with the OYI agent.
 *
 * Mirrors the browser live-chat client:
 *   - local acoustic VAD + barge-in state machine with preroll
 *   - binary frames  -> raw 16 kHz mono PCM16 microphone audio (inside a turn)
 *   - text frames    -> control messages in, server events out
 *   - "bot_audio"    -> base64 MP3 that is played back locally (ducked/cancelled on barge-in)
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
        data class Status(val text: String) : Event
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
    private var aec: AcousticEchoCanceler? = null
    private var ns: NoiseSuppressor? = null
    private var recordJob: Job? = null
    private var player: MediaPlayer? = null

    @Volatile
    private var smartTurnEnabled = false

    @Volatile
    private var agentSpeaking = false

    @Volatile
    private var agentStartedAtMs = 0L

    /** Opens the voice socket and starts streaming microphone audio. */
    @SuppressLint("MissingPermission")
    fun start(threadId: String) {
        if (_active.value) return
        val url = chat.voiceUrl()
        if (url == null) {
            Log.w(TAG, "no gateway URL configured")
            scope.launch { _events.emit(Event.Failed("Set the OYI gateway URL first")) }
            return
        }
        Log.i(TAG, "starting live voice on $url")

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

        val bufferBytes = max(minBuffer * 2, FRAME_BYTES * 8)
        val record = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferBytes,
            ).takeIf { it.state == AudioRecord.STATE_INITIALIZED }
                ?: AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferBytes,
                )
        } catch (error: Exception) {
            Log.w(TAG, "AudioRecord failed: ${error.message}")
            scope.launch { _events.emit(Event.Failed("Cannot open the microphone: ${error.message}")) }
            return
        }

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            scope.launch { _events.emit(Event.Failed("Cannot open the microphone")) }
            return
        }

        attachHardwareAudioEffects(record.audioSessionId)
        recorder = record
        record.startRecording()
        _active.value = true

        recordJob = scope.launch(Dispatchers.IO) {
            runCaptureLoop(record)
        }

        // Handshake: the server replies with "ready".
        sendControl(
            JSONObject().apply {
                put("type", "start")
                put("sample_rate", SAMPLE_RATE)
                put("mode", "agent")
                put("mood", "normal")
                put("thread_id", threadId)
                put("plain_text", true)
            }
        )
    }

    private fun attachHardwareAudioEffects(sessionId: Int) {
        runCatching {
            if (AcousticEchoCanceler.isAvailable()) {
                aec = AcousticEchoCanceler.create(sessionId)?.apply { enabled = true }
            }
        }
        runCatching {
            if (NoiseSuppressor.isAvailable()) {
                ns = NoiseSuppressor.create(sessionId)?.apply { enabled = true }
            }
        }
    }

    private suspend fun runCaptureLoop(record: AudioRecord) {
        val frame = ByteArray(FRAME_BYTES)
        val preroll = ArrayDeque<ByteArray>(PREROLL_FRAMES)
        var state = "armed" // armed | user_speaking | barge_pending | interrupted | cooldown
        var speechStartedAt = 0L
        var silenceSince = 0L
        var onsetSilenceSince = 0L
        var cooldownUntil = 0L
        var bargeEmitted = false
        var cancelEmitted = false
        var ambientNoise = NOISE_FLOOR_THRESHOLD
        var hpPrevIn = 0f
        var hpPrevOut = 0f

        fun resetSpeech() {
            speechStartedAt = 0L
            silenceSince = 0L
            onsetSilenceSince = 0L
            bargeEmitted = false
            cancelEmitted = false
        }

        fun prerollBase64(): String {
            if (preroll.isEmpty()) return ""
            val out = ByteArrayOutputStream(preroll.size * FRAME_BYTES)
            for (chunk in preroll) out.write(chunk)
            return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
        }

        while (scope.isActive && _active.value) {
            var offset = 0
            while (offset < FRAME_BYTES && _active.value) {
                val read = runCatching {
                    record.read(frame, offset, FRAME_BYTES - offset)
                }.getOrDefault(-1)
                if (read <= 0) break
                offset += read
            }
            if (offset <= 0) continue

            val pcm = frame.copyOf(offset)
            if (preroll.size >= PREROLL_FRAMES) preroll.removeFirst()
            preroll.addLast(pcm)

            // High-pass voice-band RMS (~120 Hz cutoff at 16 kHz).
            val shorts = ShortArray(offset / 2)
            ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(shorts)
            var sumSq = 0.0
            for (s in shorts) {
                val x = s / 32768f
                val y = 0.955f * (hpPrevOut + x - hpPrevIn)
                hpPrevIn = x
                hpPrevOut = y
                sumSq += (y * y).toDouble()
            }
            val rms = if (shorts.isNotEmpty()) sqrt(sumSq / shorts.size).toFloat() else 0f
            val now = SystemClock.elapsedRealtime()
            if (now < cooldownUntil) continue

            val isAgentOut = agentSpeaking
            val baseThreshold = if (isAgentOut) BARGE_SPEECH_THRESHOLD else SPEECH_THRESHOLD
            val threshold = min(MAX_SPEECH_THRESHOLD, max(baseThreshold, ambientNoise * 2.6f))
            val speaking = rms >= threshold

            if (!speaking && state == "armed" && rms < SPEECH_THRESHOLD) {
                val alpha = if (rms < ambientNoise) 0.12f else 0.02f
                ambientNoise = ambientNoise * (1f - alpha) + rms * alpha
            }

            if (isAgentOut) {
                if (now - agentStartedAtMs < ECHO_GUARD_MS) continue
                if (!speaking) {
                    if (state == "barge_pending" && !bargeEmitted) {
                        if (onsetSilenceSince == 0L) onsetSilenceSince = now
                        if (now - onsetSilenceSince >= ONSET_HANGOVER_MS) {
                            resetSpeech()
                            state = "armed"
                        }
                    } else if (state == "barge_pending" || state == "interrupted") {
                        if (silenceSince == 0L) silenceSince = now
                        val silenceLimit = if (state == "interrupted") {
                            if (smartTurnEnabled) 500L else SILENCE_MS
                        } else {
                            BARGE_HANGOVER_MS
                        }
                        if (now - silenceSince >= silenceLimit) {
                            resetSpeech()
                            state = "cooldown"
                            cooldownUntil = now + COOLDOWN_MS
                            sendControl(JSONObject().apply { put("type", "speech_end") })
                            emit(Event.Status("Thinking…"))
                        } else {
                            socket?.send(pcm.toByteString(0, pcm.size))
                        }
                    }
                    continue
                }

                silenceSince = 0L
                onsetSilenceSince = 0L
                if (speechStartedAt == 0L) {
                    speechStartedAt = now
                    state = "barge_pending"
                }
                val sustained = now - speechStartedAt
                if (!bargeEmitted && sustained >= BARGE_MIN_SPEECH_MS) {
                    bargeEmitted = true
                    duckPlayback()
                    emit(Event.Status("Go ahead…"))
                    sendControl(
                        JSONObject().apply {
                            put("type", "barge_in")
                            put("preroll", prerollBase64())
                        }
                    )
                }
                if (!cancelEmitted && sustained >= CANCEL_AFTER_MS) {
                    cancelEmitted = true
                    state = "interrupted"
                    stopPlayback(notifyServer = true)
                    sendControl(JSONObject().apply { put("type", "barge_cancel") })
                }
                if (bargeEmitted) {
                    socket?.send(pcm.toByteString(0, pcm.size))
                }
                continue
            }

            if (state == "barge_pending" || state == "interrupted") {
                state = "user_speaking"
            }

            if (state == "user_speaking") {
                socket?.send(pcm.toByteString(0, pcm.size))
                if (speaking) {
                    silenceSince = 0L
                } else {
                    if (silenceSince == 0L) silenceSince = now
                    val silenceLimit = if (smartTurnEnabled) 500L else SILENCE_MS
                    if (now - silenceSince >= silenceLimit) {
                        resetSpeech()
                        state = "cooldown"
                        cooldownUntil = now + COOLDOWN_MS
                        sendControl(JSONObject().apply { put("type", "speech_end") })
                        emit(Event.Status("Thinking…"))
                    }
                }
                continue
            }

            if (state == "cooldown") {
                state = "armed"
            }

            if (speaking) {
                onsetSilenceSince = 0L
                if (speechStartedAt == 0L) speechStartedAt = now
                if (now - speechStartedAt >= MIN_SPEECH_MS) {
                    silenceSince = 0L
                    state = "user_speaking"
                    emit(Event.Status("Hearing you…"))
                    sendControl(
                        JSONObject().apply {
                            put("type", "speech_start")
                            put("preroll", prerollBase64())
                        }
                    )
                    socket?.send(pcm.toByteString(0, pcm.size))
                }
            } else if (speechStartedAt != 0L) {
                if (onsetSilenceSince == 0L) onsetSilenceSince = now
                if (now - onsetSilenceSince >= ONSET_HANGOVER_MS) {
                    speechStartedAt = 0L
                    onsetSilenceSince = 0L
                }
            }
        }
    }

    /** Stops the microphone and closes the socket. */
    fun stop() {
        Log.i(TAG, "stopping live voice")
        _active.value = false
        recordJob?.cancel()
        recordJob = null
        runCatching { aec?.release() }
        aec = null
        runCatching { ns?.release() }
        ns = null
        runCatching { recorder?.stop() }
        runCatching { recorder?.release() }
        recorder = null
        sendControl(JSONObject().apply { put("type", "stop") })
        socket?.close(1000, "stopped")
        socket = null
        stopPlayback(notifyServer = false)
    }

    /** Plays a filler/backchannel clip (an MP3 URL). */
    fun playClip(url: String) {
        if (url.isBlank() || agentSpeaking) return
        val resolvedUrl = if (url.startsWith("http://") || url.startsWith("https://")) {
            url
        } else {
            val base = chat.baseUrl() ?: return
            "$base/${url.trimStart('/')}"
        }
        scope.launch {
            withContext(Dispatchers.Main) {
                runCatching {
                    stopPlayback(notifyServer = false)
                    player = MediaPlayer().apply {
                        setDataSource(resolvedUrl)
                        setVolume(0.6f, 0.6f)
                        setOnPreparedListener { it.start() }
                        setOnCompletionListener { stopPlayback(notifyServer = false) }
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
                    stopPlayback(notifyServer = false)
                    val file = File(context.cacheDir, "oyi_reply_${System.currentTimeMillis()}.mp3")
                    file.writeBytes(bytes)
                    agentSpeaking = true
                    agentStartedAtMs = SystemClock.elapsedRealtime()
                    sendControl(JSONObject().apply { put("type", "playback_start") })
                    emit(Event.Status("Speaking…"))
                    player = MediaPlayer().apply {
                        setDataSource(file.absolutePath)
                        setVolume(1.0f, 1.0f)
                        setOnPreparedListener { it.start() }
                        setOnCompletionListener {
                            stopPlayback(notifyServer = true)
                            runCatching { file.delete() }
                            if (_active.value) emit(Event.Status("Listening…"))
                        }
                        prepareAsync()
                    }
                }.onFailure {
                    Log.w(TAG, "Reply playback failed: ${it.message}")
                    stopPlayback(notifyServer = true)
                }
            }
        }
    }

    private fun duckPlayback() {
        scope.launch(Dispatchers.Main) {
            runCatching { player?.setVolume(0.22f, 0.22f) }
        }
    }

    private fun unduckPlayback() {
        scope.launch(Dispatchers.Main) {
            runCatching { player?.setVolume(1.0f, 1.0f) }
        }
    }

    private fun stopPlayback(notifyServer: Boolean) {
        val wasSpeaking = agentSpeaking
        agentSpeaking = false
        runCatching { player?.stop() }
        runCatching { player?.release() }
        player = null
        if (notifyServer && wasSpeaking) {
            sendControl(JSONObject().apply { put("type", "playback_end") })
        }
    }

    private fun sendControl(payload: JSONObject) {
        socket?.send(payload.toString())
    }

    private val listener = object : WebSocketListener() {

        override fun onOpen(webSocket: WebSocket, response: Response) {
            Log.i(TAG, "voice socket open")
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            val payload = runCatching { JSONObject(text) }.getOrNull() ?: return
            when (payload.optString("type")) {
                "ready" -> {
                    smartTurnEnabled = payload.optBoolean("smart_turn", false)
                    emit(
                        Event.Ready(
                            sampleRate = payload.optInt("sample_rate", SAMPLE_RATE),
                            smartTurn = smartTurnEnabled,
                        )
                    )
                }
                "partial" -> emit(Event.Partial(payload.optString("text")))
                "eou" -> {
                    val verdict = payload.optString("verdict", "turn")
                    val transcript = payload.optString("text").trim()
                    if (verdict == "turn" && transcript.isNotEmpty()) {
                        emit(Event.UserText(transcript))
                    } else {
                        emit(Event.Partial(""))
                    }
                }
                "user_text" -> emit(Event.UserText(payload.optString("text")))
                "continuation", "resume" -> {
                    emit(Event.Partial(""))
                    unduckPlayback()
                    emit(Event.Status(if (agentSpeaking) "Speaking…" else "Listening…"))
                }
                "turn_hold" -> emit(Event.Status("Go on…"))
                "turn_resumed" -> emit(Event.Status("Hearing you…"))
                "bot_text_delta" -> emit(Event.BotText(payload.optString("text")))
                "bot_audio" -> {
                    val audio = runCatching {
                        Base64.decode(payload.optString("audio"), Base64.DEFAULT)
                    }.getOrNull()
                    if (audio != null) {
                        emit(Event.BotAudio(payload.optString("text"), audio))
                    }
                }
                "filler", "backchannel" -> {
                    emit(Event.Filler(payload.optString("phrase"), payload.optString("url")))
                }
                "error" -> {
                    Log.w(TAG, "server error: ${payload.optString("message")}")
                    emit(Event.Failed(payload.optString("message", "Voice error")))
                }
                "turn_end" -> emit(Event.TurnEnded)
                "interrupted" -> {
                    stopPlayback(notifyServer = false)
                    emit(Event.Status("Go ahead…"))
                }
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
        private const val FRAME_BYTES = 640 // 20 ms at 16 kHz mono PCM16
        private const val PREROLL_FRAMES = 16 // ~320 ms preroll
        private const val SPEECH_THRESHOLD = 0.020f
        private const val BARGE_SPEECH_THRESHOLD = 0.015f
        private const val NOISE_FLOOR_THRESHOLD = 0.008f
        private const val MAX_SPEECH_THRESHOLD = 0.045f
        private const val MIN_SPEECH_MS = 180L
        private const val BARGE_MIN_SPEECH_MS = 160L
        private const val ONSET_HANGOVER_MS = 90L
        private const val BARGE_HANGOVER_MS = 380L
        private const val SILENCE_MS = 700L
        private const val COOLDOWN_MS = 160L
        private const val ECHO_GUARD_MS = 320L
        private const val CANCEL_AFTER_MS = 680L
    }
}
