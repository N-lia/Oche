package ai.oyi.device.ui

import android.app.Application
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import ai.oyi.device.OyiApplication
import ai.oyi.device.accessibility.OyiAccessibilityService
import ai.oyi.device.model.AppUsage
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.ForegroundApp
import ai.oyi.device.service.OyiService
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate

data class UiState(
    val overlayPermission: Boolean = false,
    val usagePermission: Boolean = false,
    val accessibilityEnabled: Boolean = false,
    val notificationPermission: Boolean = false,
    val overlayEnabled: Boolean = false,
    val autoStart: Boolean = false,
    val gatewayUrl: String = "",
    val token: String = "",
    val deviceId: String = "",
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val connectionError: String = "",
    val busy: Boolean = false,
    val lastMessage: String = "",
    val currentApp: ForegroundApp? = null,
    val topApps: List<AppUsage> = emptyList(),
)

class MainViewModel(application: Application) : AndroidViewModel(application) {

    private val container = (application as OyiApplication).container
    private val context = application.applicationContext

    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            container.settings.settings.collect { settings ->
                _state.update {
                    it.copy(
                        overlayEnabled = settings.overlayEnabled,
                        autoStart = settings.autoStart,
                        gatewayUrl = settings.gatewayUrl,
                        token = settings.token,
                        deviceId = settings.deviceId,
                    )
                }
            }
        }
        viewModelScope.launch {
            container.runtime.foreground.collect { app ->
                _state.update { it.copy(currentApp = app) }
            }
        }
        viewModelScope.launch {
            container.runtime.connection.collect { connection ->
                _state.update { it.copy(connection = connection) }
            }
        }
        viewModelScope.launch {
            container.runtime.connectionError.collect { error ->
                _state.update { it.copy(connectionError = error) }
            }
        }
        viewModelScope.launch {
            container.runtime.busy.collect { busy ->
                _state.update { it.copy(busy = busy) }
            }
        }
        viewModelScope.launch {
            container.runtime.lastMessage.collect { message ->
                _state.update { it.copy(lastMessage = message) }
            }
        }
        refresh()
    }

    /** Re-reads permissions and usage; called on every resume. */
    fun refresh() {
        viewModelScope.launch {
            val settings = container.settings.settings.value
            val overlayPermission = Settings.canDrawOverlays(context)
            val usagePermission = hasUsageAccess()
            val accessibilityEnabled = isAccessibilityEnabled()
            val notificationPermission = hasNotificationPermission()
            val topApps = withContext(Dispatchers.IO) { loadTopApps() }
            _state.update {
                it.copy(
                    overlayPermission = overlayPermission,
                    usagePermission = usagePermission,
                    accessibilityEnabled = accessibilityEnabled,
                    notificationPermission = notificationPermission,
                    overlayEnabled = settings.overlayEnabled,
                    autoStart = settings.autoStart,
                    gatewayUrl = settings.gatewayUrl,
                    token = settings.token,
                    deviceId = settings.deviceId,
                    topApps = topApps,
                )
            }
        }
    }

    fun saveGateway(url: String, token: String) {
        container.settings.saveGateway(url, token)
        refresh()
    }

    fun setAutoStart(enabled: Boolean) {
        container.settings.setAutoStart(enabled)
        refresh()
    }

    /** Starts or stops the OYI service (and therefore the overlay bubble). */
    fun setOverlayEnabled(enabled: Boolean) {
        if (enabled) {
            if (!Settings.canDrawOverlays(context)) {
                _state.update { it.copy(lastMessage = "Allow 'Display over other apps' first.") }
                return
            }
            container.settings.setOverlayEnabled(true)
            try {
                ContextCompat.startForegroundService(
                    context,
                    Intent(context, OyiService::class.java),
                )
            } catch (error: Exception) {
                _state.update { it.copy(lastMessage = "Could not start OYI: ${error.message}") }
            }
        } else {
            container.settings.setOverlayEnabled(false)
            context.stopService(Intent(context, OyiService::class.java))
        }
        refresh()
    }

    private fun loadTopApps(): List<AppUsage> {
        val day = LocalDate.now().toString()
        return container.usage.topApps(day, limit = 8).map { (packageName, totalMs) ->
            AppUsage(
                packageName = packageName,
                label = container.runtime.labelFor(packageName),
                totalMs = totalMs,
            )
        }
    }

    private fun hasUsageAccess(): Boolean {
        val appOps = context.getSystemService(android.app.AppOpsManager::class.java)
            ?: return false
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(
                android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
                android.os.Process.myUid(),
                context.packageName,
            )
        }
        return mode == android.app.AppOpsManager.MODE_ALLOWED
    }

    private fun isAccessibilityEnabled(): Boolean {
        val expected = "${context.packageName}/${OyiAccessibilityService::class.java.name}"
        val enabled = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
        ).orEmpty()
        return enabled.split(':').any { it.equals(expected, ignoreCase = true) }
    }

    private fun hasNotificationPermission(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
    }
}
