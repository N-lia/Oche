package ai.oyi.device.focus

import android.accessibilityservice.AccessibilityService
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.PowerManager
import android.os.SystemClock
import android.view.inputmethod.InputMethodManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import ai.oyi.device.R
import ai.oyi.device.accessibility.OyiAccessibilityService
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.runtime.DeviceRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.ZoneId

/** What the UI needs to draw the blocker's live state. */
data class FocusLive(
    val now: Long = System.currentTimeMillis(),
    val verified: Boolean = false,
    val lock: LockStatus = LockStatus(LockPhase.OFF),
    val drift: DriftState = DriftState(),
    val usedToday: Map<String, Long> = emptyMap(),
)

/**
 * Enforces the block rules on the phone.
 *
 * It lives for the whole process (the accessibility service keeps that alive)
 * and makes every decision locally: no network call sits between noticing a
 * distraction and blocking it.
 */
class FocusGuard(
    context: Context,
    private val store: FocusStore,
    private val clock: TrustedClock,
    private val runtime: DeviceRuntime,
    private val settings: SettingsStore,
) {

    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val power = appContext.getSystemService(Context.POWER_SERVICE) as? PowerManager

    private val _live = MutableStateFlow(FocusLive())
    val live: StateFlow<FocusLive> = _live.asStateFlow()

    private var ticker: Job? = null
    private var currentPackage = ""
    private var drift = DriftState()
    private var driftSession: FocusSession? = null
    private var usage = DayUsage()
    private var lastTickElapsed = 0L
    private var ticksSinceSave = 0
    private var lastSyncElapsed = 0L
    private var lastBlockPackage = ""
    private var lastBlockElapsed = 0L
    private var ignoredPackages: Set<String> = emptySet()

    /** True when there is anything to check in a browser's address bar. */
    val watchesSites: Boolean
        get() = store.state.value.sites.isNotEmpty()

    fun start() {
        if (ticker != null) return
        usage = store.readUsage()
        ignoredPackages = loadIgnoredPackages()
        createChannel()
        lastTickElapsed = SystemClock.elapsedRealtime()
        ticker = scope.launch {
            while (isActive) {
                runCatching { tick() }
                delay(TICK_MS)
            }
        }
    }

    /** Called by the accessibility service whenever a window comes forward. */
    fun onWindowEvent(packageName: String) {
        if (packageName.isBlank() || packageName in ignoredPackages) return
        if (packageName == currentPackage) return
        currentPackage = packageName
        // Do not wait for the next tick to block something already blocked.
        runCatching { enforceCurrentApp(store.state.value) }
    }

    /** Called by the accessibility service with the site a browser shows. */
    fun onBrowserHost(packageName: String, host: String) {
        val state = store.state.value
        val reason = FocusEngine.siteVerdict(state, host) ?: return
        block(packageName, host, reason)
    }

    private fun tick() {
        val elapsed = SystemClock.elapsedRealtime()
        val delta = (elapsed - lastTickElapsed).coerceIn(0L, MAX_TICK_MS)
        lastTickElapsed = elapsed

        val trusted = clock.now()
        store.expireEmergencyExit(trusted.ms)
        var state = store.state.value

        // Allowances reset at local midnight. An unverified clock may lag
        // after a reboot, so the day then follows the phone's own clock.
        val dayClock = if (trusted.verified) trusted.ms else System.currentTimeMillis()
        val day = Instant.ofEpochMilli(dayClock).atZone(ZoneId.systemDefault()).toLocalDate().toString()
        if (usage.day != day) {
            usage = DayUsage(day = day)
            store.saveUsage(usage)
        }

        val session = state.session
        if (session != null && trusted.ms >= session.endsAt) {
            store.endSession()
            state = store.state.value
            announce("Block complete", "\"${session.task.ifBlank { "Focus" }}\" is done. Nice work.")
        }
        if (state.session != driftSession) {
            driftSession = state.session
            drift = DriftState()
        }

        val interactive = power?.isInteractive ?: true
        val inDistraction = interactive && state.distraction(currentPackage) != null
        if (inDistraction && delta > 0L) {
            val used = (usage.usedMs[currentPackage] ?: 0L) + delta
            usage = usage.copy(usedMs = usage.usedMs + (currentPackage to used))
        }

        val activeSession = state.session
        if (activeSession != null && interactive) {
            val before = drift.stage
            drift = drift.advance(inDistraction, delta, state.drift)
            if (drift.stage > before) {
                val line = NudgeVoice.nudge(state.voice, drift.stage, activeSession.task)
                announce(line.title, line.body)
            }
        }

        if (interactive) enforceCurrentApp(state)

        ticksSinceSave += 1
        if (ticksSinceSave >= SAVE_EVERY_TICKS) {
            ticksSinceSave = 0
            store.saveUsage(usage)
            clock.checkpoint()
        }
        maybeSyncClock(elapsed, trusted.verified)

        _live.value = FocusLive(
            now = trusted.ms,
            verified = trusted.verified,
            lock = MonthlyLock.status(state.lock, trusted.ms, trusted.verified),
            drift = drift,
            usedToday = usage.usedMs,
        )
    }

    private fun enforceCurrentApp(state: FocusState) {
        val packageName = currentPackage
        if (packageName.isBlank() || packageName == appContext.packageName) return
        val reason = FocusEngine.appVerdict(
            state = state,
            packageName = packageName,
            usedTodayMs = usage.usedMs[packageName] ?: 0L,
            lockdown = drift.stage >= DriftState.STAGE_LOCKDOWN,
        ) ?: return
        block(packageName, runtime.labelFor(packageName), reason)
    }

    private fun block(packageName: String, label: String, reason: BlockReason) {
        val elapsed = SystemClock.elapsedRealtime()
        if (packageName == lastBlockPackage && elapsed - lastBlockElapsed < BLOCK_DEBOUNCE_MS) return
        lastBlockPackage = packageName
        lastBlockElapsed = elapsed

        val service = OyiAccessibilityService.instance
        // Send the blocked app to the background, then cover it.
        service?.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
        val intent = Intent(appContext, BlockActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
            .putExtra(BlockActivity.EXTRA_LABEL, label)
            .putExtra(BlockActivity.EXTRA_PACKAGE, packageName)
            .putExtra(BlockActivity.EXTRA_REASON, reason.name)
        runCatching { (service ?: appContext).startActivity(intent) }
    }

    private fun maybeSyncClock(elapsed: Long, verified: Boolean) {
        val interval = if (verified) SYNC_INTERVAL_MS else RETRY_SYNC_MS
        if (lastSyncElapsed != 0L && elapsed - lastSyncElapsed < interval) return
        lastSyncElapsed = elapsed
        scope.launch { clock.sync(settings.settings.value.gatewayUrl) }
    }

    /** Shows a nudge on the Dynamic Island and as a notification. */
    private fun announce(title: String, body: String) {
        runtime.showNudge(title, body)
        runCatching {
            val notification = NotificationCompat.Builder(appContext, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_stat_oyi)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(NotificationCompat.BigTextStyle().bigText(body))
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setAutoCancel(true)
                .build()
            NotificationManagerCompat.from(appContext).notify(NOTIFICATION_ID, notification)
        }
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = appContext.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Focus nudges",
            NotificationManager.IMPORTANCE_HIGH,
        ).apply {
            description = "Nudges when you drift during a focus block."
        }
        manager.createNotificationChannel(channel)
    }

    /** Keyboards and system chrome come forward without the user leaving an app. */
    private fun loadIgnoredPackages(): Set<String> {
        val ignored = mutableSetOf("android", "com.android.systemui")
        runCatching {
            val input = appContext.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
            input?.enabledInputMethodList?.forEach { ignored.add(it.packageName) }
        }
        return ignored
    }

    private companion object {
        const val TICK_MS = 1_000L
        const val MAX_TICK_MS = 5_000L
        const val SAVE_EVERY_TICKS = 15
        const val BLOCK_DEBOUNCE_MS = 1_500L
        const val SYNC_INTERVAL_MS = 6L * 60L * 60L * 1000L
        const val RETRY_SYNC_MS = 5L * 60L * 1000L
        const val CHANNEL_ID = "oyi_focus"
        const val NOTIFICATION_ID = 4310
    }
}
