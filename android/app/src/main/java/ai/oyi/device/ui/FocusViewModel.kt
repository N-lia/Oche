package ai.oyi.device.ui

import android.app.Application
import android.content.Intent
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import ai.oyi.device.OyiApplication
import ai.oyi.device.focus.FocusLive
import ai.oyi.device.focus.FocusState
import ai.oyi.device.focus.Voice
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class AppEntry(val packageName: String, val label: String)

class FocusViewModel(application: Application) : AndroidViewModel(application) {

    private val container = (application as OyiApplication).container
    private val store = container.focusStore

    val state: StateFlow<FocusState> = store.state
    val live: StateFlow<FocusLive> = container.focusGuard.live
    val accessibilityReady: StateFlow<Boolean> = container.runtime.accessibilityReady

    private val _apps = MutableStateFlow<List<AppEntry>>(emptyList())
    val apps: StateFlow<List<AppEntry>> = _apps.asStateFlow()

    private val _notice = MutableStateFlow("")
    val notice: StateFlow<String> = _notice.asStateFlow()

    fun label(packageName: String): String = container.runtime.labelFor(packageName)

    fun clearNotice() {
        _notice.value = ""
    }

    /** Loads the apps that have a launcher icon, for the picker. */
    fun loadApps() {
        viewModelScope.launch {
            _apps.value = withContext(Dispatchers.IO) {
                val context = getApplication<Application>()
                val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                runCatching {
                    context.packageManager.queryIntentActivities(launcher, 0)
                        .map { it.activityInfo.packageName }
                        .distinct()
                        .filter { it != context.packageName }
                        .map { AppEntry(it, label(it)) }
                        .sortedBy { it.label.lowercase() }
                }.getOrDefault(emptyList())
            }
        }
    }

    fun setDistraction(packageName: String, dailyLimitMin: Int) {
        if (!store.setDistraction(packageName, dailyLimitMin)) locked()
    }

    fun removeDistraction(packageName: String) {
        if (!store.removeDistraction(packageName)) locked()
    }

    fun addSite(raw: String): Boolean {
        val added = store.addSite(raw)
        if (!added) _notice.value = "That does not look like a website. Try youtube.com."
        return added
    }

    fun removeSite(host: String) {
        if (!store.removeSite(host)) locked()
    }

    fun setVoice(voice: Voice) {
        store.setVoice(voice)
    }

    fun startSession(task: String, minutes: Int) {
        store.startSession(task, minutes)
    }

    fun finishSession() {
        store.endSession()
    }

    /** The lock needs a clock the user cannot move, so it syncs once first. */
    fun armLock() {
        viewModelScope.launch {
            val clock = container.clock
            if (!clock.everSynced) {
                val synced = clock.sync(container.settings.settings.value.gatewayUrl)
                if (!synced) {
                    _notice.value = "Go online once so the lock can set its clock."
                    return@launch
                }
            }
            store.armLock()
        }
    }

    fun disarmLock() {
        if (!store.disarmLock()) locked()
    }

    fun requestEmergencyExit() {
        store.requestEmergencyExit()
    }

    fun cancelEmergencyExit() {
        store.cancelEmergencyExit()
    }

    private fun locked() {
        _notice.value = "Locked. Loosening opens on the 1st of the month."
    }
}
