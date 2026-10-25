package ai.oyi.device.net

import ai.oyi.device.data.SettingsStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * Text chat with the OYI agent.
 *
 * The agent running on the server owns the tools, so a message like
 * "open Settings on my phone" reaches `control_phone` and drives the paired
 * device. The app only talks to the same webapp that serves the phone link.
 */
class ChatClient(private val settings: SettingsStore) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(180, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    /** The http(s) base URL of the OYI webapp, derived from the gateway URL. */
    fun baseUrl(): String? {
        val stored = settings.settings.value.gatewayUrl.trim()
        if (stored.isEmpty()) return null
        val withScheme = when {
            stored.startsWith("https://") -> stored
            stored.startsWith("http://") -> stored
            stored.startsWith("wss://") -> "https://" + stored.removePrefix("wss://")
            stored.startsWith("ws://") -> "http://" + stored.removePrefix("ws://")
            else -> "https://$stored"
        }
        return withScheme.removeSuffix("/").substringBefore("/ws/device")
    }

    /** WebSocket URL for the live voice loop. */
    fun voiceUrl(): String? {
        val base = baseUrl() ?: return null
        val ws = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> "wss://$base"
        }
        return "$ws/ws/voice"
    }

    /** Sends one message and returns the agent's reply. */
    suspend fun send(prompt: String, threadId: String): Result<String> = withContext(Dispatchers.IO) {
        val base = baseUrl()
        if (base == null) {
            return@withContext Result.failure(IllegalStateException("Set the OYI gateway URL first"))
        }
        val payload = JSONObject().apply {
            put("prompt", prompt)
            put("thread_id", threadId)
            // The agent writes Telegram HTML; the app wants plain text.
            put("plain", true)
        }
        val request = Request.Builder()
            .url("$base/api/chat")
            .post(payload.toString().toRequestBody("application/json".toMediaTypeOrNull()))
            .build()
        try {
            http.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    return@withContext Result.failure(
                        IllegalStateException("OYI replied ${response.code}: ${body.take(200)}")
                    )
                }
                val text = runCatching { JSONObject(body).optString("response") }.getOrDefault(body)
                Result.success(text)
            }
        } catch (error: Exception) {
            Result.failure(error)
        }
    }

    /**
     * Streams the agent's reply token by token.
     *
     * The server answers with newline-delimited JSON: {"text": "..."} per
     * chunk and a final {"done": true}.
     */
    fun stream(prompt: String, threadId: String): Flow<String> = flow {
        val base = baseUrl()
        if (base == null) {
            throw IllegalStateException("Set the OYI gateway URL first")
        }
        val payload = JSONObject().apply {
            put("prompt", prompt)
            put("thread_id", threadId)
            put("plain", true)
        }
        val request = Request.Builder()
            .url("$base/api/chat/stream")
            .post(payload.toString().toRequestBody("application/json".toMediaTypeOrNull()))
            .build()

        http.newCall(request).execute().use { response ->
            if (response.code == 404) {
                // Older server without the streaming endpoint.
                val fallback = send(prompt, threadId).getOrThrow()
                if (fallback.isNotEmpty()) emit(fallback)
                return@use
            }
            if (!response.isSuccessful) {
                val body = response.body?.string().orEmpty()
                throw IllegalStateException("OYI replied ${response.code}: ${body.take(200)}")
            }
            val source = response.body?.source() ?: return@use
            while (!source.exhausted()) {
                val line = source.readUtf8Line() ?: break
                if (!line.startsWith("{")) continue
                val obj = runCatching { JSONObject(line) }.getOrNull() ?: continue
                obj.optString("error").takeIf { it.isNotBlank() }?.let {
                    throw IllegalStateException(it)
                }
                val text = obj.optString("text")
                if (text.isNotEmpty()) emit(text)
            }
        }
    }.flowOn(Dispatchers.IO)
}
