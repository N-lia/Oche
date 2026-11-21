package ai.oyi.device.data

import android.app.AppOpsManager
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import android.content.Context
import android.os.Build
import android.os.Process
import ai.oyi.device.model.UsageSession
import ai.oyi.device.runtime.DeviceRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate
import java.time.ZoneId

/**
 * Reads foreground-app usage from UsageStatsManager.
 *
 * Accessibility events are the fast path for "which app is open"; this tracker
 * is the durable path for "how long have I spent in each app".  Both write to
 * the local SQLite database.
 */
class UsageTracker(
    private val context: Context,
    private val repository: UsageRepository,
    private val runtime: DeviceRuntime,
    private val scope: CoroutineScope,
) {

    private val usageStats: UsageStatsManager? =
        context.getSystemService(Context.USAGE_STATS_SERVICE) as? UsageStatsManager

    private var job: Job? = null

    fun start() {
        if (job != null) return
        job = scope.launch(Dispatchers.IO) {
            while (isActive) {
                sync()
                delay(SYNC_INTERVAL_MS)
            }
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }

    /** Runs one sync pass (called on start, screen events and periodically). */
    suspend fun sync() {
        val manager = usageStats ?: return
        if (!hasUsageAccess()) return

        val now = System.currentTimeMillis()
        val dayStart = startOfToday()
        val day = LocalDate.now().toString()

        withContext(Dispatchers.IO) {
            val totals = HashMap<String, Long>()
            try {
                val stats = manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, dayStart, now)
                stats?.forEach { entry ->
                    val total = entry.totalTimeInForeground
                    if (total > 0 && !isIgnored(entry.packageName)) {
                        totals[entry.packageName] = total
                    }
                }
            } catch (_: Exception) {
                // UsageStatsManager can throw when the permission is revoked mid-query.
            }
            repository.replaceDailyTotals(day, totals, now)

            val sessions = readSessions(manager, dayStart, now)
            repository.replaceSessionsSince(dayStart, sessions)

            val latest = sessions.lastOrNull()
            if (latest != null && latest.endedAt >= now - STALE_AFTER_MS) {
                runtime.onForeground(latest.packageName, latest.startedAt)
            }

            repository.prune(now - RETENTION_MS)
        }
    }

    private fun readSessions(
        manager: UsageStatsManager,
        from: Long,
        to: Long,
    ): List<UsageSession> {
        val sessions = mutableListOf<UsageSession>()
        var currentPackage: String? = null
        var currentStart = 0L

        try {
            val events = manager.queryEvents(from, to) ?: return sessions
            val event = UsageEvents.Event()
            while (events.hasNextEvent()) {
                events.getNextEvent(event)
                val timestamp = event.timeStamp
                val packageName = event.packageName ?: continue
                when (event.eventType) {
                    UsageEvents.Event.ACTIVITY_RESUMED -> {
                        if (currentPackage != null && currentPackage != packageName) {
                            sessions.add(
                                UsageSession(currentPackage, currentStart, timestamp, timestamp - currentStart),
                            )
                        }
                        if (currentPackage != packageName) {
                            currentPackage = packageName
                            currentStart = timestamp
                        }
                    }
                    UsageEvents.Event.ACTIVITY_PAUSED -> {
                        if (currentPackage == packageName) {
                            sessions.add(
                                UsageSession(currentPackage, currentStart, timestamp, timestamp - currentStart),
                            )
                            currentPackage = null
                        }
                    }
                    UsageEvents.Event.SCREEN_NON_INTERACTIVE,
                    UsageEvents.Event.KEYGUARD_SHOWN,
                    -> {
                        if (currentPackage != null) {
                            sessions.add(
                                UsageSession(currentPackage, currentStart, timestamp, timestamp - currentStart),
                            )
                            currentPackage = null
                        }
                    }
                }
            }
        } catch (_: Exception) {
            return sessions
        }

        if (currentPackage != null) {
            val duration = (to - currentStart).coerceAtLeast(0L)
            sessions.add(UsageSession(currentPackage, currentStart, to, duration))
        }

        return sessions.filter { it.durationMs >= MIN_SESSION_MS && !isIgnored(it.packageName) }
    }

    /** True when the user granted "Usage access" to OYI. */
    fun hasUsageAccess(): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as? AppOpsManager ?: return false
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.packageName,
            )
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS,
                Process.myUid(),
                context.packageName,
            )
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private fun isIgnored(packageName: String): Boolean =
        packageName == context.packageName || packageName in IGNORED_PACKAGES

    private fun startOfToday(): Long = LocalDate.now()
        .atStartOfDay(ZoneId.systemDefault())
        .toInstant()
        .toEpochMilli()

    companion object {
        private const val SYNC_INTERVAL_MS = 30_000L
        private const val STALE_AFTER_MS = 90_000L
        private const val MIN_SESSION_MS = 1_000L
        private const val RETENTION_MS = 30L * 24L * 60L * 60L * 1000L

        private val IGNORED_PACKAGES = setOf(
            "com.android.systemui",
            "com.android.launcher",
            "com.android.launcher3",
            "com.google.android.apps.nexuslauncher",
            "com.miui.home",
        )
    }
}
