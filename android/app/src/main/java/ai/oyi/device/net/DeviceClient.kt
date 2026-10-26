package ai.oyi.device.net

import android.content.Context
import android.os.Build
import android.util.Log
import ai.oyi.device.BuildConfig
import ai.oyi.device.accessibility.ExecutionResult
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.runtime.DeviceRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.util.concurrent.TimeUnit
import kotlin.math.min

/**
 * Outbound WebSocket to the OYI device gateway.
 *
 * The phone dials out, so the server never needs an inbound port on the
 * device.  Frames follow the protocol in `device_protocol.py`:
 *
 *   phone -> server   hello
 *   server -> phone   hello_ack
 *   server -> phone   command
 *   phone -> server   observation
 *   server -> phone   action
 *   server -> phone   command_done
 */
class DeviceClient(
    private val context: Context,
    private val settings: SettingsStore,
    private val runtime: DeviceRuntime,
    private val usage: UsageRepository,
) {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private var webSocket: WebSocket? = null
    private var settingsJob: Job? = null
    private var reconnectJob: Job? = null
    private var reconnectAttempt = 0
    private var stopped = false
    private var connectedUrl: String? = null
    private var connectedToken: String? = null
    private var targetUrl: String = ""

    private var activeCommand: ActiveCommand? = null

    fun start() {
        if (settingsJob != null) return
        stopped = false
        settingsJob = scope.launch {
            settings.settings.collect { current ->
                if (stopped) return@collect
                val changed = current.gatewayUrl != connectedUrl || current.token != connectedToken
                if (changed || webSocket == null) {
                    reconnect(current)
                }
            }
        }
    }

    fun stop() {
        stopped = true
        settingsJob?.cancel()
        settingsJob = null
        reconnectJob?.cancel()
        reconnectJob = null
        webSocket?.close(1000, "stopped")
        webSocket = null
        connectedUrl = null
        connectedToken = null
        activeCommand = null
        runtime.setConnection(ConnectionState.DISCONNECTED)
    }

    private fun reconnect(config: ai.oyi.device.model.DeviceSettings) {
        reconnectJob?.cancel()
        // Detach the old socket first: its close callback must not schedule a
        // reconnect, otherwise every reconnect would trigger the next one and
        // the link would flap once per second.
        val previous = webSocket
        webSocket = null
        previous?.close(1000, "reconnecting")

        val url = config.gatewayUrl
        val token = config.token
        if (url.isBlank() || token.isBlank()) {
            connectedUrl = url
            connectedToken = token
            runtime.setConnection(
                ConnectionState.DISCONNECTED,
                if (url.isBlank()) "Gateway URL not set" else "Pairing token not set",
            )
            return
        }

        val target = websocketUrl(url) ?: run {
            runtime.setConnection(ConnectionState.ERROR, "Invalid gateway URL")
            return
        }

        connectedUrl = url
        connectedToken = token
        targetUrl = target
        runtime.setConnection(ConnectionState.CONNECTING)
        Log.i(TAG, "connecting to $target")

        val request = Request.Builder()
            .url(target)
            .header("Authorization", "Bearer $token")
            .header("X-OYI-Device-Id", config.deviceId)
            .build()

        webSocket = http.newWebSocket(request, listener)
    }

    private val listener = object : WebSocketListener() {

        /** Ignores callbacks from a socket we already replaced. */
        private fun isCurrent(webSocket: WebSocket): Boolean = webSocket === this@DeviceClient.webSocket

        override fun onOpen(webSocket: WebSocket, response: Response) {
            if (!isCurrent(webSocket)) return
            reconnectAttempt = 0
            runtime.setConnection(ConnectionState.CONNECTED)
            Log.i(TAG, "connected to $targetUrl")
            send(
                JSONObject().apply {
                    put("type", "hello")
                    put("device_id", settings.settings.value.deviceId.ifBlank { "android-unknown" })
                    put("name", Build.MODEL)
                    put("model", Build.MODEL)
                    put("android_sdk", Build.VERSION.SDK_INT)
                    put("app_version", BuildConfig.VERSION_NAME)
                    put(
                        "capabilities",
                        org.json.JSONArray().apply {
                            put("overlay")
                            put("usage_stats")
                            put("accessibility")
                            put("screenshot")
                        },
                    )
                },
            )
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            if (!isCurrent(webSocket)) return
            scope.launch { handleMessage(text) }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            if (!isCurrent(webSocket)) return
            val reason = describe(t)
            Log.w(TAG, "connection failed: $reason", t)
            runtime.setConnection(ConnectionState.ERROR, reason)
            // The link is gone, so any command in flight is dead. Keeping it
            // would make the phone refuse every future command as "busy".
            dropActiveCommand()
            scheduleReconnect()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            if (!isCurrent(webSocket)) return
            Log.i(TAG, "connection closed: $code $reason")
            runtime.setConnection(ConnectionState.DISCONNECTED, reason)
            dropActiveCommand()
            scheduleReconnect()
        }
    }

    /** Forgets a command that cannot finish any more. */
    private fun dropActiveCommand() {
        activeCommand = null
        runtime.setBusy(false)
        runtime.setMessage("Link lost")
    }

    /** Turns transport exceptions into something a person can act on. */
    private fun describe(error: Throwable): String = when (error) {
        is java.net.UnknownHostException -> "Gateway host not found"
        is java.net.ConnectException -> "Cannot reach the gateway"
        is javax.net.ssl.SSLException -> "TLS handshake failed"
        else -> error.message?.takeIf { it.isNotBlank() } ?: "Connection failed"
    }

    private fun scheduleReconnect() {
        if (stopped) return
        if (reconnectJob?.isActive == true) return
        reconnectAttempt += 1
        val delayMs = min(30_000L, 1_000L * (1L shl min(reconnectAttempt - 1, 5)))
        reconnectJob = scope.launch {
            delay(delayMs)
            if (stopped) return@launch
            reconnect(settings.settings.value)
        }
    }

    private fun handleMessage(text: String) {
        val payload = try {
            JSONObject(text)
        } catch (_: Exception) {
            return
        }

        when (payload.optString("type")) {
            "hello_ack" -> runtime.setMessage("Connected to OYI")

            "command" -> startCommand(payload)

            "action" -> executeAction(payload)

            "command_done" -> finishCommand(payload.optString("message", "Done."))

            "cancel" -> {
                activeCommand = null
                runtime.setBusy(false)
                runtime.setMessage(payload.optString("message", "Cancelled"))
            }

            "status_request" -> respondWithStatus(payload)

            "error" -> {
                runtime.setMessage(payload.optString("message", "Gateway error"))
                activeCommand = null
                runtime.setBusy(false)
            }
        }
    }

    private fun startCommand(payload: JSONObject) {
        val commandId = payload.optString("command_id")
        if (commandId.isBlank()) return

        if (activeCommand != null) {
            send(
                JSONObject().apply {
                    put("type", "command_done")
                    put("command_id", commandId)
                    put("status", "busy")
                    put("message", "The phone is already running another command.")
                },
            )
            return
        }

        activeCommand = ActiveCommand(
            id = commandId,
            text = payload.optString("text"),
            step = 0,
        )
        runtime.setBusy(true)
        runtime.setMessage("Working: ${activeCommand?.text ?: ""}")
        sendObservation()
    }

    private fun executeAction(payload: JSONObject) {
        val command = activeCommand ?: return
        if (payload.optString("command_id") != command.id) return

        // The server labels an action with the observation step it came from.
        // Ignoring anything else prevents a retried action from running twice.
        val actionStep = payload.optInt("step", command.step)
        if (actionStep != command.step) return

        val action = payload.optJSONObject("action") ?: return
        scope.launch {
            val controller = runtime.controller
            if (controller == null) {
                command.lastActionOk = false
                command.lastError = "The OYI accessibility service is not enabled."
                sendObservation()
                return@launch
            }
            val ok = try {
                controller.execute(action)
            } catch (error: Exception) {
                ExecutionResult(false, error.message ?: "Action failed")
            }
            command.lastActionOk = ok.ok
            command.lastError = if (ok.ok) "" else ok.message
            command.lastResult = ok.message
            command.step += 1
            delay(ACTION_SETTLE_MS)
            sendObservation()
        }
    }

    private fun finishCommand(message: String) {
        activeCommand = null
        runtime.setBusy(false)
        runtime.setMessage(message)
    }

    /**
     * Answers a status request with the current app and today's usage, so chat
     * can answer "what am I using?" without a vision round trip.
     */
    private fun respondWithStatus(payload: JSONObject) {
        val requestId = payload.optString("request_id")
        if (requestId.isBlank()) return

        scope.launch {
            val foreground = runtime.foreground.value
            val day = LocalDate.now().toString()
            val data = withContext(Dispatchers.IO) {
                val top = usage.topApps(day, limit = 6).map { (packageName, totalMs) ->
                    JSONObject().apply {
                        put("package", packageName)
                        put("label", runtime.labelFor(packageName))
                        put("ms", totalMs)
                    }
                }
                val currentTotal = foreground?.let {
                    usage.totalFor(day, it.packageName)
                } ?: 0L
                JSONObject().apply {
                    put("type", "status_response")
                    put("request_id", requestId)
                    put("package", foreground?.packageName.orEmpty())
                    put("app_label", foreground?.label.orEmpty())
                    put(
                        "elapsed_ms",
                        foreground?.let { System.currentTimeMillis() - it.since } ?: 0L,
                    )
                    put("today_ms", currentTotal)
                    put("screen_on", isScreenOn())
                    put("top_apps", JSONArray().apply { top.forEach { put(it) } })
                }
            }
            send(data)
        }
    }

    private fun isScreenOn(): Boolean {
        val power = context.getSystemService(android.content.Context.POWER_SERVICE)
            as? android.os.PowerManager ?: return true
        return power.isInteractive
    }

    private fun sendObservation() {
        val command = activeCommand ?: return
        scope.launch {
            val controller = runtime.controller
            if (controller == null) {
                // Without the accessibility service there is no screen to look
                // at; report that instead of sending an empty observation the
                // model would have to guess about.
                command.lastActionOk = false
                command.lastError = "The OYI accessibility service is not enabled."
            }
            val snapshot = try {
                controller?.snapshot()
            } catch (error: Exception) {
                command.lastActionOk = false
                command.lastError = error.message ?: "Could not read the screen"
                null
            }

            val foreground = runtime.foreground.value
            Log.i(
                TAG,
                "step=${command.step} elements=${snapshot?.elements?.size ?: 0} " +
                    "list=${snapshot?.elementText?.lineSequence()?.take(3)?.joinToString(" | ")}",
            )
            val message = JSONObject().apply {
                put("type", "observation")
                put("command_id", command.id)
                put("step", command.step)
                put("package", snapshot?.packageName ?: foreground?.packageName.orEmpty())
                put("app_label", foreground?.label.orEmpty())
                put("elements", snapshot?.elementJson ?: JSONArray())
                put("element_text", snapshot?.elementText.orEmpty())
                put("screenshot", snapshot?.screenshot.orEmpty())
                put("width", snapshot?.width ?: 0)
                put("height", snapshot?.height ?: 0)
                put("last_action_ok", command.lastActionOk)
                put("error", command.lastError)
                put("action_result", command.lastResult)
            }
            send(message)
        }
    }

    private fun send(payload: JSONObject) {
        webSocket?.send(payload.toString())
    }

    private data class ActiveCommand(
        val id: String,
        val text: String,
        var step: Int = 0,
        var lastActionOk: Boolean = true,
        var lastError: String = "",
        var lastResult: String = "",
    )

    companion object {
        private const val TAG = "OyiLink"
        private const val ACTION_SETTLE_MS = 700L

        /** Converts a user-entered base URL into the device WebSocket URL. */
        fun websocketUrl(base: String): String? {
            var url = base.trim()
            if (url.isEmpty()) return null
            url = url.removeSuffix("/")
            val withScheme = when {
                url.startsWith("https://") -> "wss://" + url.removePrefix("https://")
                url.startsWith("http://") -> "ws://" + url.removePrefix("http://")
                url.startsWith("wss://") || url.startsWith("ws://") -> url
                else -> "wss://$url"
            }
            if (!withScheme.contains("://")) return null
            return if (withScheme.endsWith("/ws/device")) {
                withScheme
            } else {
                "$withScheme/ws/device"
            }
        }
    }
}
