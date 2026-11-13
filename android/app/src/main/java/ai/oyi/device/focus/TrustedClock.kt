package ai.oyi.device.focus

import android.content.Context
import android.os.SystemClock
import android.provider.Settings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * The clock the monthly lock runs on.
 *
 * It is anchored to a server's time and carried forward by the monotonic
 * clock, so changing the phone's date does not move it. See [TrustedTime].
 */
class TrustedClock(context: Context) {

    private val appContext = context.applicationContext
    private val prefs = appContext.getSharedPreferences("oyi_clock", Context.MODE_PRIVATE)

    private val client = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .build()

    private var anchor: ClockAnchor? = readAnchor()
    private var highWater: Long = prefs.getLong(KEY_HIGH_WATER, 0L)

    /** True once the clock has been set from a server at least once. */
    val everSynced: Boolean
        @Synchronized get() = anchor != null

    @Synchronized
    fun now(): TrustedNow {
        val elapsed = SystemClock.elapsedRealtime()
        val boot = bootCount()
        val current = anchor
        val resolved = TrustedTime.resolve(
            anchor = current,
            highWater = highWater,
            elapsedNow = elapsed,
            bootNow = boot,
            wallNow = System.currentTimeMillis(),
        )
        if (current != null) {
            val rebooted = current.boot != boot || elapsed < current.elapsed
            if (rebooted) {
                // Carry the lower bound forward from here on this boot.
                writeAnchor(ClockAnchor(resolved.ms, elapsed, boot, verified = false))
            }
            if (resolved.ms > highWater) highWater = resolved.ms
        }
        return resolved
    }

    /** Saves the latest time seen, so a reboot cannot rewind the clock. */
    @Synchronized
    fun checkpoint() {
        if (anchor != null) prefs.edit().putLong(KEY_HIGH_WATER, highWater).apply()
    }

    /** Reads the time from the gateway (or a public endpoint) `Date` header. */
    suspend fun sync(gatewayUrl: String): Boolean = withContext(Dispatchers.IO) {
        val urls = buildList {
            httpBase(gatewayUrl)?.let { add(it) }
            add(FALLBACK_URL)
        }
        for (url in urls) {
            val serverTime = runCatching { fetchDate(url) }.getOrNull() ?: continue
            if (serverTime < MIN_SANE_TIME) continue
            setFromServer(serverTime)
            return@withContext true
        }
        false
    }

    private fun fetchDate(url: String): Long? {
        val started = SystemClock.elapsedRealtime()
        val request = Request.Builder().url(url).head().build()
        client.newCall(request).execute().use { response ->
            val date = response.headers.getDate("Date") ?: return null
            val roundTrip = SystemClock.elapsedRealtime() - started
            return date.time + roundTrip / 2
        }
    }

    @Synchronized
    private fun setFromServer(serverTime: Long) {
        writeAnchor(
            ClockAnchor(
                wall = serverTime,
                elapsed = SystemClock.elapsedRealtime(),
                boot = bootCount(),
                verified = true,
            ),
        )
        highWater = serverTime
        prefs.edit().putLong(KEY_HIGH_WATER, highWater).apply()
    }

    private fun writeAnchor(value: ClockAnchor) {
        anchor = value
        prefs.edit()
            .putLong(KEY_WALL, value.wall)
            .putLong(KEY_ELAPSED, value.elapsed)
            .putInt(KEY_BOOT, value.boot)
            .putBoolean(KEY_VERIFIED, value.verified)
            .apply()
    }

    private fun readAnchor(): ClockAnchor? {
        if (!prefs.contains(KEY_WALL)) return null
        return ClockAnchor(
            wall = prefs.getLong(KEY_WALL, 0L),
            elapsed = prefs.getLong(KEY_ELAPSED, 0L),
            boot = prefs.getInt(KEY_BOOT, 0),
            verified = prefs.getBoolean(KEY_VERIFIED, false),
        )
    }

    private fun bootCount(): Int = try {
        Settings.Global.getInt(appContext.contentResolver, Settings.Global.BOOT_COUNT, 0)
    } catch (_: Exception) {
        0
    }

    private fun httpBase(gatewayUrl: String): String? {
        val trimmed = gatewayUrl.trim().trimEnd('/')
        if (trimmed.isEmpty()) return null
        return when {
            trimmed.startsWith("wss://") -> "https://" + trimmed.removePrefix("wss://")
            trimmed.startsWith("ws://") -> "http://" + trimmed.removePrefix("ws://")
            trimmed.startsWith("http://") || trimmed.startsWith("https://") -> trimmed
            else -> "https://$trimmed"
        }
    }

    private companion object {
        const val KEY_WALL = "anchor_wall"
        const val KEY_ELAPSED = "anchor_elapsed"
        const val KEY_BOOT = "anchor_boot"
        const val KEY_VERIFIED = "anchor_verified"
        const val KEY_HIGH_WATER = "high_water"
        const val FALLBACK_URL = "https://www.google.com/generate_204"

        /** 1 Jan 2025; anything earlier is a broken server clock. */
        const val MIN_SANE_TIME = 1_735_689_600_000L
    }
}
