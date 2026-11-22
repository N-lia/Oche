package ai.oyi.device.update

import ai.oyi.device.BuildConfig
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.provider.Settings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

/** One published build: CI names releases "build-<n>" and attaches the APK. */
data class Release(val build: Int, val apkUrl: String, val sizeBytes: Long, val notes: String)

sealed interface UpdateState {
    data object Idle : UpdateState
    data object Checking : UpdateState
    data object UpToDate : UpdateState
    data class Available(val release: Release) : UpdateState
    data class Downloading(val release: Release, val progress: Float) : UpdateState
    data class Ready(val release: Release) : UpdateState
    data object Installing : UpdateState
    data class Failed(val message: String) : UpdateState
}

/** "build-123" -> 123; anything else is not a build. */
fun buildNumberOf(tag: String): Int? = tag.removePrefix("build-").takeIf { tag.startsWith("build-") }?.toIntOrNull()

/** Reads the latest release's JSON (GitHub's releases/latest response). */
fun parseRelease(json: String): Release? {
    val root = JSONObject(json)
    val build = buildNumberOf(root.optString("tag_name")) ?: return null
    val assets = root.optJSONArray("assets") ?: return null
    for (i in 0 until assets.length()) {
        val asset = assets.getJSONObject(i)
        if (asset.optString("name").endsWith(".apk")) {
            return Release(
                build = build,
                apkUrl = asset.optString("browser_download_url"),
                sizeBytes = asset.optLong("size"),
                notes = root.optString("body").trim(),
            )
        }
    }
    return null
}

/**
 * Over-the-air updates from the repository's GitHub releases.
 *
 * Check: ask for the latest release; if its build number beats ours, offer
 * it. Download: fetch the APK into the cache. Install: hand it to Android's
 * package installer, which asks the person to confirm. Android only accepts
 * it when it is signed with the same key as the installed app (CI signs
 * every build with the repo's fixed debug key).
 */
class AppUpdater(private val context: Context) {

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    private val _state = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val state: StateFlow<UpdateState> = _state.asStateFlow()

    val currentBuild: Int = BuildConfig.VERSION_CODE
    val currentVersion: String = BuildConfig.VERSION_NAME

    private var lastCheck = 0L

    /** Checks now, or only if the last check is old enough when [quiet]. */
    suspend fun check(quiet: Boolean = false) {
        val busy = _state.value is UpdateState.Downloading || _state.value is UpdateState.Installing
        if (busy) return
        if (quiet && System.currentTimeMillis() - lastCheck < QUIET_INTERVAL_MS) return
        lastCheck = System.currentTimeMillis()
        if (!quiet) _state.value = UpdateState.Checking
        val result = withContext(Dispatchers.IO) {
            runCatching {
                val request = Request.Builder()
                    .url(LATEST_URL)
                    .header("Accept", "application/vnd.github+json")
                    .build()
                http.newCall(request).execute().use { response ->
                    if (response.code == 404) return@runCatching null
                    if (!response.isSuccessful) error("GitHub said ${response.code}")
                    parseRelease(response.body?.string().orEmpty())
                }
            }
        }
        _state.value = result.fold(
            onSuccess = { release ->
                when {
                    release == null || release.build <= currentBuild -> UpdateState.UpToDate
                    downloaded(release).exists() -> UpdateState.Ready(release)
                    else -> UpdateState.Available(release)
                }
            },
            onFailure = { if (quiet) UpdateState.Idle else UpdateState.Failed("Couldn't check: ${it.message ?: "no connection"}") },
        )
    }

    suspend fun download(release: Release) {
        _state.value = UpdateState.Downloading(release, 0f)
        val target = downloaded(release)
        val result = withContext(Dispatchers.IO) {
            runCatching {
                target.parentFile?.mkdirs()
                val partial = File(target.path + ".part")
                http.newCall(Request.Builder().url(release.apkUrl).build()).execute().use { response ->
                    if (!response.isSuccessful) error("download failed (${response.code})")
                    val body = response.body ?: error("empty download")
                    val total = body.contentLength().takeIf { it > 0 } ?: release.sizeBytes
                    body.byteStream().use { input ->
                        partial.outputStream().use { output ->
                            val buffer = ByteArray(64 * 1024)
                            var read = 0L
                            var lastShown = 0f
                            while (true) {
                                val n = input.read(buffer)
                                if (n < 0) break
                                output.write(buffer, 0, n)
                                read += n
                                if (total > 0) {
                                    val p = (read.toFloat() / total).coerceIn(0f, 1f)
                                    if (p - lastShown >= 0.01f) {
                                        lastShown = p
                                        _state.value = UpdateState.Downloading(release, p)
                                    }
                                }
                            }
                        }
                    }
                }
                check(partial.renameTo(target)) { "couldn't save the update" }
            }
        }
        _state.value = result.fold(
            onSuccess = { UpdateState.Ready(release) },
            onFailure = { UpdateState.Failed("Download failed: ${it.message ?: "no connection"}") },
        )
    }

    /** Android must allow OYI to install apps before it can update itself. */
    fun canInstall(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O || context.packageManager.canRequestPackageInstalls()

    fun openInstallPermission() {
        context.startActivity(
            Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        )
    }

    suspend fun install(release: Release) {
        val apk = downloaded(release)
        if (!apk.exists()) {
            _state.value = UpdateState.Available(release)
            return
        }
        _state.value = UpdateState.Installing
        val result = withContext(Dispatchers.IO) {
            runCatching {
                val installer = context.packageManager.packageInstaller
                val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                    setAppPackageName(context.packageName)
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                        setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                    }
                }
                val sessionId = installer.createSession(params)
                installer.openSession(sessionId).use { session ->
                    session.openWrite("oyi.apk", 0, apk.length()).use { out ->
                        apk.inputStream().use { it.copyTo(out) }
                        session.fsync(out)
                    }
                    val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                        (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
                    val callback = PendingIntent.getBroadcast(
                        context,
                        sessionId,
                        Intent(context, UpdateInstallReceiver::class.java).setPackage(context.packageName),
                        flags,
                    )
                    session.commit(callback.intentSender)
                }
            }
        }
        result.onFailure { _state.value = UpdateState.Failed("Install failed: ${it.message ?: "unknown error"}") }
    }

    /** The installer's answer, from [UpdateInstallReceiver]. */
    internal fun onInstallStatus(status: Int, message: String?) {
        if (status == PackageInstaller.STATUS_SUCCESS || status == PackageInstaller.STATUS_PENDING_USER_ACTION) return
        _state.value = when (status) {
            PackageInstaller.STATUS_FAILURE_ABORTED -> UpdateState.Idle
            PackageInstaller.STATUS_FAILURE_CONFLICT, PackageInstaller.STATUS_FAILURE_INCOMPATIBLE ->
                UpdateState.Failed("This build is signed differently from the one installed. Install it once by hand, then updates work.")
            else -> UpdateState.Failed("Install failed: ${message ?: "status $status"}")
        }
    }

    private fun downloaded(release: Release) = File(context.cacheDir, "updates/oyi-build-${release.build}.apk")

    companion object {
        const val LATEST_URL = "https://api.github.com/repos/N-lia/Oche/releases/latest"
        private const val QUIET_INTERVAL_MS = 6 * 60 * 60 * 1000L
    }
}

/** Receives the package installer's result, and shows its confirm screen when it asks. */
class UpdateInstallReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            @Suppress("DEPRECATION")
            val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT)
            if (confirm != null) {
                context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
        }
        val app = context.applicationContext as? ai.oyi.device.OyiApplication ?: return
        app.container.updater.onInstallStatus(status, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE))
    }
}
