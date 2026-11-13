package ai.oyi.device.focus

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject

/**
 * The blocker's rules, stored on the phone.
 *
 * Every change goes through [change], which refuses anything that loosens the
 * blocker while the monthly lock is shut. The UI and, later, the agent API
 * both come through here, so neither can talk its way around the lock.
 */
class FocusStore(context: Context, private val clock: TrustedClock) {

    private val prefs = context.applicationContext
        .getSharedPreferences("oyi_focus", Context.MODE_PRIVATE)

    private val _state = MutableStateFlow(readState())
    val state: StateFlow<FocusState> = _state.asStateFlow()

    fun lockStatus(): LockStatus {
        val now = clock.now()
        return MonthlyLock.status(_state.value.lock, now.ms, now.verified)
    }

    /** Adds an app, or tightens/loosens one that is already listed. */
    fun setDistraction(packageName: String, dailyLimitMin: Int): Boolean = change { state ->
        val entry = Distraction(packageName, dailyLimitMin)
        if (state.distraction(packageName) == null) {
            state.copy(distractions = state.distractions + entry)
        } else {
            state.copy(
                distractions = state.distractions.map {
                    if (it.packageName == packageName) entry else it
                },
            )
        }
    }

    fun removeDistraction(packageName: String): Boolean = change { state ->
        state.copy(distractions = state.distractions.filterNot { it.packageName == packageName })
    }

    fun addSite(raw: String): Boolean {
        val host = BrowserUrls.hostOf(raw) ?: return false
        return change { state ->
            if (host in state.sites) state else state.copy(sites = state.sites + host)
        }
    }

    fun removeSite(host: String): Boolean = change { state ->
        state.copy(sites = state.sites - host)
    }

    fun setVoice(voice: Voice): Boolean = change { it.copy(voice = voice) }

    fun startSession(task: String, minutes: Int): Boolean {
        val now = clock.now().ms
        return change { state ->
            if (state.session != null) {
                state
            } else {
                state.copy(
                    session = FocusSession(
                        task = task.trim(),
                        startedAt = now,
                        endsAt = now + minutes.coerceIn(1, 600) * 60_000L,
                    ),
                )
            }
        }
    }

    fun endSession(): Boolean = change { it.copy(session = null) }

    fun armLock(): Boolean {
        val now = clock.now().ms
        return change { state ->
            if (state.lock.armed) state else state.copy(lock = LockConfig(armed = true, armedAt = now))
        }
    }

    /** Loosening: only goes through while the window is open. */
    fun disarmLock(): Boolean = change { it.copy(lock = LockConfig()) }

    fun requestEmergencyExit(): Boolean {
        val now = clock.now().ms
        return change { state ->
            if (!state.lock.armed || state.lock.emergencyRequestedAt > 0L) {
                state
            } else {
                state.copy(lock = state.lock.copy(emergencyRequestedAt = now))
            }
        }
    }

    fun cancelEmergencyExit(): Boolean = change { state ->
        state.copy(lock = state.lock.copy(emergencyRequestedAt = 0L))
    }

    /** Drops an emergency exit whose window has passed. */
    fun expireEmergencyExit(now: Long) {
        if (MonthlyLock.emergencyExpired(_state.value.lock, now)) cancelEmergencyExit()
    }

    @Synchronized
    private fun change(transform: (FocusState) -> FocusState): Boolean {
        val old = _state.value
        val new = transform(old)
        if (new == old) return true
        if (MonthlyLock.isLoosening(old, new) && !lockStatus().canLoosen) return false
        prefs.edit().putString(KEY_STATE, encode(new).toString()).apply()
        _state.value = new
        return true
    }

    fun readUsage(): DayUsage {
        val raw = prefs.getString(KEY_USAGE, null) ?: return DayUsage()
        return try {
            val json = JSONObject(raw)
            val used = HashMap<String, Long>()
            val apps = json.optJSONObject("apps") ?: JSONObject()
            for (key in apps.keys()) used[key] = apps.optLong(key, 0L)
            DayUsage(day = json.optString("day"), usedMs = used)
        } catch (_: Exception) {
            DayUsage()
        }
    }

    fun saveUsage(usage: DayUsage) {
        val apps = JSONObject()
        usage.usedMs.forEach { (packageName, ms) -> apps.put(packageName, ms) }
        val json = JSONObject().put("day", usage.day).put("apps", apps)
        prefs.edit().putString(KEY_USAGE, json.toString()).apply()
    }

    private fun readState(): FocusState {
        val raw = prefs.getString(KEY_STATE, null) ?: return FocusState()
        return try {
            decode(JSONObject(raw))
        } catch (_: Exception) {
            FocusState()
        }
    }

    private fun encode(state: FocusState): JSONObject {
        val apps = JSONArray()
        state.distractions.forEach {
            apps.put(JSONObject().put("package", it.packageName).put("limit", it.dailyLimitMin))
        }
        val sites = JSONArray()
        state.sites.forEach { sites.put(it) }
        val json = JSONObject()
            .put("apps", apps)
            .put("sites", sites)
            .put("voice", state.voice.name)
            .put(
                "lock",
                JSONObject()
                    .put("armed", state.lock.armed)
                    .put("armed_at", state.lock.armedAt)
                    .put("emergency_at", state.lock.emergencyRequestedAt),
            )
            .put(
                "drift",
                JSONObject()
                    .put("first", state.drift.firstNudgeSec)
                    .put("second", state.drift.secondNudgeSec)
                    .put("lockdown", state.drift.lockdownSec),
            )
        state.session?.let {
            json.put(
                "session",
                JSONObject()
                    .put("task", it.task)
                    .put("started_at", it.startedAt)
                    .put("ends_at", it.endsAt),
            )
        }
        return json
    }

    private fun decode(json: JSONObject): FocusState {
        val apps = mutableListOf<Distraction>()
        val appArray = json.optJSONArray("apps") ?: JSONArray()
        for (index in 0 until appArray.length()) {
            val item = appArray.optJSONObject(index) ?: continue
            val packageName = item.optString("package")
            if (packageName.isNotBlank()) {
                apps.add(Distraction(packageName, item.optInt("limit", LIMIT_FOCUS_ONLY)))
            }
        }
        val sites = mutableListOf<String>()
        val siteArray = json.optJSONArray("sites") ?: JSONArray()
        for (index in 0 until siteArray.length()) {
            val site = siteArray.optString(index)
            if (site.isNotBlank()) sites.add(site)
        }
        val lock = json.optJSONObject("lock")
        val drift = json.optJSONObject("drift")
        val session = json.optJSONObject("session")
        val defaults = DriftConfig()
        return FocusState(
            distractions = apps,
            sites = sites,
            session = session?.let {
                FocusSession(
                    task = it.optString("task"),
                    startedAt = it.optLong("started_at"),
                    endsAt = it.optLong("ends_at"),
                )
            },
            lock = LockConfig(
                armed = lock?.optBoolean("armed", false) ?: false,
                armedAt = lock?.optLong("armed_at", 0L) ?: 0L,
                emergencyRequestedAt = lock?.optLong("emergency_at", 0L) ?: 0L,
            ),
            voice = runCatching { Voice.valueOf(json.optString("voice")) }
                .getOrDefault(Voice.BIG_BROTHER),
            drift = DriftConfig(
                firstNudgeSec = drift?.optInt("first", defaults.firstNudgeSec) ?: defaults.firstNudgeSec,
                secondNudgeSec = drift?.optInt("second", defaults.secondNudgeSec) ?: defaults.secondNudgeSec,
                lockdownSec = drift?.optInt("lockdown", defaults.lockdownSec) ?: defaults.lockdownSec,
            ),
        )
    }

    private companion object {
        const val KEY_STATE = "state"
        const val KEY_USAGE = "usage"
    }
}
