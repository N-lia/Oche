package ai.oyi.device.runtime

import android.content.Context
import android.content.pm.PackageManager
import ai.oyi.device.accessibility.DeviceController
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.ForegroundApp
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Process-wide state shared by the foreground service, the accessibility
 * service and the UI.  It is intentionally tiny: everything expensive lives in
 * the service that owns it.
 */
class DeviceRuntime(context: Context) {

    private val appContext = context.applicationContext
    private val packageManager: PackageManager = appContext.packageManager

    private val _foreground = MutableStateFlow<ForegroundApp?>(null)
    val foreground: StateFlow<ForegroundApp?> = _foreground.asStateFlow()

    private val _connection = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connection: StateFlow<ConnectionState> = _connection.asStateFlow()

    private val _connectionError = MutableStateFlow("")
    val connectionError: StateFlow<String> = _connectionError.asStateFlow()

    private val _accessibilityReady = MutableStateFlow(false)
    val accessibilityReady: StateFlow<Boolean> = _accessibilityReady.asStateFlow()

    private val _busy = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = _busy.asStateFlow()

    private val _lastMessage = MutableStateFlow("")
    val lastMessage: StateFlow<String> = _lastMessage.asStateFlow()

    /**
     * The Dynamic Island has two layers. The task layer (phone control,
     * approvals, focus nudges, alerts) wins whenever it shows something; the
     * agent layer (a chat or live-voice turn) shows otherwise. [island] is
     * what the overlay and the in-app header draw.
     */
    private val _island = MutableStateFlow(IslandState())
    val island: StateFlow<IslandState> = _island.asStateFlow()

    /**
     * The layer running behind the one in [island], when both are active
     * (for example the agent talking while phone control runs). The island
     * shows it as a small second bubble; [swapIsland] brings it forward.
     */
    private val _islandSecondary = MutableStateFlow<IslandState?>(null)
    val islandSecondary: StateFlow<IslandState?> = _islandSecondary.asStateFlow()

    private var task = IslandState()
    private var agent = IslandState()
    private val islandLock = Any()

    /** The user swapped the agent in front of a running task. */
    private var preferAgent = false

    /** Microphone level while live voice is on, 0..1. Drives the island's bars. */
    private val _voiceLevel = MutableStateFlow(0f)
    val voiceLevel: StateFlow<Float> = _voiceLevel.asStateFlow()

    /** True while OYI's own screen is in front: the overlay steps aside then. */
    private val _appVisible = MutableStateFlow(false)
    val appVisible: StateFlow<Boolean> = _appVisible.asStateFlow()

    private val timers = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var settleJob: Job? = null
    private var taskTimer: Job? = null

    /** Live voice is on: the agent layer rests on LISTENING, not IDLE. */
    @Volatile
    private var liveOn = false

    /** Set by the accessibility service; used to execute UI actions. */
    @Volatile
    var controller: DeviceController? = null

    /** Callback wired by DeviceClient when the user approves/denies on the Dynamic Island. */
    @Volatile
    var onApprovalDecision: ((commandId: String, approved: Boolean) -> Unit)? = null

    /** Callback wired by DeviceClient when the user taps Stop on the Dynamic Island. */
    @Volatile
    var onCancelCommand: (() -> Unit)? = null

    /** Callback wired by live voice: a double tap on the island cuts the spoken reply short. */
    @Volatile
    var onInterruptAgent: (() -> Unit)? = null

    fun onForeground(packageName: String, at: Long = System.currentTimeMillis()) {
        if (packageName.isBlank() || packageName == appContext.packageName) return
        val current = _foreground.value
        if (current?.packageName == packageName) return
        _foreground.value = ForegroundApp(
            packageName = packageName,
            label = labelFor(packageName),
            since = at,
        )
    }

    fun clearForeground() {
        _foreground.value = null
    }

    fun onAccessibilityReady(ready: Boolean) {
        _accessibilityReady.value = ready
        if (!ready) controller = null
    }

    fun setConnection(state: ConnectionState, error: String = "") {
        _connection.value = state
        _connectionError.value = error
        if (state != ConnectionState.CONNECTED) {
            _busy.value = false
            if (task.kind == IslandKind.WORKING) setTask(IslandState())
        }
    }

    fun setBusy(busy: Boolean) {
        _busy.value = busy
    }

    fun setMessage(message: String) {
        _lastMessage.value = message
    }

    fun showWorking(commandId: String, title: String, subtitle: String, step: Int = 0) {
        _busy.value = true
        _lastMessage.value = subtitle.ifBlank { title }
        setTask(IslandState(
            kind = IslandKind.WORKING,
            title = title,
            subtitle = subtitle,
            step = step,
            commandId = commandId,
        ))
    }

    fun showCompleted(title: String, message: String, commandId: String = "") {
        _busy.value = false
        _lastMessage.value = message.ifBlank { title }
        setTask(IslandState(
            kind = IslandKind.COMPLETED,
            title = title,
            subtitle = message,
            commandId = commandId,
        ))
    }

    fun showApprovalRequired(commandId: String, title: String, prompt: String) {
        _lastMessage.value = prompt
        setTask(IslandState(
            kind = IslandKind.APPROVAL_REQUIRED,
            title = title.ifBlank { "Approval Required" },
            subtitle = prompt,
            commandId = commandId,
        ))
    }

    fun showError(title: String, message: String, commandId: String = "") {
        _busy.value = false
        _lastMessage.value = message.ifBlank { title }
        setTask(IslandState(
            kind = IslandKind.ERROR,
            title = title,
            subtitle = message,
            commandId = commandId,
        ))
    }

    /** A focus nudge: shown like an alert, dismissed by the user or on its own. */
    fun showNudge(title: String, message: String) {
        _lastMessage.value = message.ifBlank { title }
        setTask(IslandState(
            kind = IslandKind.ERROR,
            title = title,
            subtitle = message,
        ))
    }

    /** Closes whatever card is showing (a swipe or tap on the island). */
    fun dismissIsland() {
        synchronized(islandLock) {
            if (task.kind != IslandKind.IDLE) {
                task = IslandState()
            } else if (!agent.kind.isAgentBusy()) {
                settleJob?.cancel()
                agent = restingState(liveOn)
            }
            publish()
        }
    }

    fun resolveApproval(approved: Boolean) {
        val current = task
        val commandId = current.commandId
        if (approved && commandId.isNotBlank()) {
            showWorking(commandId, "Approved", "Continuing task…", current.step)
        } else {
            showError("Action Denied", "You rejected the action.", commandId)
        }
        onApprovalDecision?.invoke(commandId, approved)
    }

    /** Double tap on the island while OYI speaks. */
    fun interruptAgent() {
        onInterruptAgent?.invoke()
    }

    /** Brings the activity in the second bubble to the front, and back. */
    fun swapIsland() {
        synchronized(islandLock) {
            preferAgent = !preferAgent
            publish()
        }
    }

    fun cancelActiveCommand() {
        onCancelCommand?.invoke()
        showError("Task Stopped", "Cancelled from Dynamic Island.")
    }

    // ------------------------------------------------------------ agent layer
    // Called by the chat screen and live voice. Each turn runs
    // LISTENING -> THINKING -> TOOL* -> REPLYING / SPEAKING -> COMPLETED.

    fun agentListening(caption: String = "Listening") {
        liveOn = true
        setAgent(IslandState(kind = IslandKind.LISTENING, title = caption, fromAgent = true))
    }

    fun agentThinking(caption: String = "Thinking…") {
        setAgent(busy(IslandKind.THINKING, caption))
    }

    fun agentTool(label: String, done: Boolean) {
        setAgent(busy(IslandKind.TOOL, label, tool = label).copy(toolDone = done))
    }

    fun agentReplying() {
        if (agent.kind == IslandKind.SPEAKING || agent.kind == IslandKind.REPLYING) return
        setAgent(busy(IslandKind.REPLYING, "Replying…"))
    }

    fun agentSpeaking() {
        setAgent(busy(IslandKind.SPEAKING, "Speaking"))
    }

    /** The turn is over: flash "Done" (time and tools), then settle. */
    fun agentTurnDone(listening: Boolean) {
        liveOn = listening
        val started = agent.startedAt
        val tools = agent.tools
        if (!agent.kind.isAgentBusy()) {
            agentRest(listening)
            return
        }
        setAgent(
            IslandState(
                kind = IslandKind.COMPLETED,
                title = "Done",
                subtitle = toolSummary(tools),
                elapsedMs = if (started > 0) System.currentTimeMillis() - started else 0L,
                tools = tools,
                fromAgent = true,
            )
        )
        settleJob = timers.launch {
            delay(DONE_HOLD_MS)
            synchronized(islandLock) {
                if (agent.kind == IslandKind.COMPLETED) {
                    agent = restingState(listening)
                    publish()
                }
            }
        }
    }

    /** Back to listening (live voice on) or to the resting pill. */
    fun agentRest(listening: Boolean) {
        liveOn = listening
        setAgent(restingState(listening))
    }

    fun agentError(message: String) {
        setAgent(
            IslandState(kind = IslandKind.ERROR, title = "Something went wrong", subtitle = message, fromAgent = true)
        )
        settleJob = timers.launch {
            delay(ERROR_HOLD_MS)
            synchronized(islandLock) {
                if (agent.kind == IslandKind.ERROR) {
                    agent = IslandState()
                    publish()
                }
            }
        }
    }

    fun setVoiceLevel(level: Float) {
        _voiceLevel.value = level.coerceIn(0f, 1f)
    }

    fun setAppVisible(visible: Boolean) {
        _appVisible.value = visible
    }

    /** A busy state; a turn already under way keeps its timer and tools. */
    private fun busy(kind: IslandKind, title: String, tool: String? = null): IslandState {
        val continuing = agent.kind.isAgentBusy()
        val started = if (continuing && agent.startedAt > 0) agent.startedAt else System.currentTimeMillis()
        val earlier = if (continuing) agent.tools else emptyList()
        val tools = if (tool == null || tool in earlier) earlier else earlier + tool
        return IslandState(kind = kind, title = title, startedAt = started, tools = tools, fromAgent = true)
    }

    private fun restingState(listening: Boolean): IslandState =
        if (listening) {
            IslandState(kind = IslandKind.LISTENING, title = "Listening", fromAgent = true)
        } else {
            IslandState()
        }

    private fun setAgent(state: IslandState) {
        synchronized(islandLock) {
            settleJob?.cancel()
            settleJob = null
            agent = state
            publish()
        }
    }

    private fun setTask(state: IslandState) {
        synchronized(islandLock) {
            taskTimer?.cancel()
            taskTimer = null
            task = state
            publish()
            // Results and alerts fold away on their own; approvals wait.
            if (state.kind == IslandKind.COMPLETED || state.kind == IslandKind.ERROR) {
                taskTimer = timers.launch {
                    delay(TASK_HOLD_MS)
                    synchronized(islandLock) {
                        if (task === state) {
                            task = IslandState()
                            publish()
                        }
                    }
                }
            }
        }
    }

    private fun publish() {
        val taskOn = task.kind != IslandKind.IDLE
        val agentOn = agent.kind != IslandKind.IDLE
        if (!(taskOn && agentOn)) preferAgent = false
        // An approval always comes to the front: it is waiting on the user.
        val agentFront = taskOn && agentOn && preferAgent && task.kind != IslandKind.APPROVAL_REQUIRED
        val front = if (!taskOn || agentFront) agent else task
        _island.value = front
        _islandSecondary.value = when {
            !(taskOn && agentOn) -> null
            front === agent -> task
            else -> agent
        }
    }

    fun labelFor(packageName: String): String {
        return try {
            val info = packageManager.getApplicationInfo(packageName, 0)
            packageManager.getApplicationLabel(info).toString()
        } catch (_: PackageManager.NameNotFoundException) {
            packageName
        } catch (_: Exception) {
            packageName
        }
    }

    companion object {
        private const val DONE_HOLD_MS = 1_800L
        private const val ERROR_HOLD_MS = 4_000L
        private const val TASK_HOLD_MS = 6_500L

        fun toolSummary(tools: List<String>): String =
            if (tools.isEmpty()) "" else tools.joinToString(" · ")
    }
}

/** True for the states of a turn in progress. */
fun IslandKind.isAgentBusy(): Boolean = when (this) {
    IslandKind.THINKING, IslandKind.TOOL, IslandKind.REPLYING, IslandKind.SPEAKING -> true
    else -> false
}
