package ai.oyi.device.runtime

import android.content.Context
import android.content.pm.PackageManager
import ai.oyi.device.accessibility.DeviceController
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.ForegroundApp
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

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

    private val _island = MutableStateFlow(IslandState())
    val island: StateFlow<IslandState> = _island.asStateFlow()

    /** Set by the accessibility service; used to execute UI actions. */
    @Volatile
    var controller: DeviceController? = null

    /** Callback wired by DeviceClient when the user approves/denies on the Dynamic Island. */
    @Volatile
    var onApprovalDecision: ((commandId: String, approved: Boolean) -> Unit)? = null

    /** Callback wired by DeviceClient when the user taps Stop on the Dynamic Island. */
    @Volatile
    var onCancelCommand: (() -> Unit)? = null

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
            if (_island.value.kind == IslandKind.WORKING) {
                _island.value = IslandState(kind = IslandKind.IDLE)
            }
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
        _island.value = IslandState(
            kind = IslandKind.WORKING,
            title = title,
            subtitle = subtitle,
            step = step,
            commandId = commandId,
        )
    }

    fun showCompleted(title: String, message: String, commandId: String = "") {
        _busy.value = false
        _lastMessage.value = message.ifBlank { title }
        _island.value = IslandState(
            kind = IslandKind.COMPLETED,
            title = title,
            subtitle = message,
            commandId = commandId,
        )
    }

    fun showApprovalRequired(commandId: String, title: String, prompt: String) {
        _lastMessage.value = prompt
        _island.value = IslandState(
            kind = IslandKind.APPROVAL_REQUIRED,
            title = title.ifBlank { "Approval Required" },
            subtitle = prompt,
            commandId = commandId,
        )
    }

    fun showError(title: String, message: String, commandId: String = "") {
        _busy.value = false
        _lastMessage.value = message.ifBlank { title }
        _island.value = IslandState(
            kind = IslandKind.ERROR,
            title = title,
            subtitle = message,
            commandId = commandId,
        )
    }

    fun dismissIsland() {
        _island.value = IslandState(kind = IslandKind.IDLE)
    }

    fun resolveApproval(approved: Boolean) {
        val current = _island.value
        val commandId = current.commandId
        if (approved && commandId.isNotBlank()) {
            showWorking(commandId, "Approved", "Continuing task…", current.step)
        } else {
            showError("Action Denied", "You rejected the action.", commandId)
        }
        onApprovalDecision?.invoke(commandId, approved)
    }

    fun cancelActiveCommand() {
        onCancelCommand?.invoke()
        showError("Task Stopped", "Cancelled from Dynamic Island.")
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
}
