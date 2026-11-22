package ai.oyi.device.service

import androidx.compose.ui.Alignment
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.foundation.layout.Box
import android.content.ComponentCallbacks
import android.content.Context
import android.content.Intent
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Build
import android.provider.Settings
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.ViewCompositionStrategy
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import ai.oyi.device.MainActivity
import ai.oyi.device.accessibility.OyiAccessibilityService
import ai.oyi.device.data.SettingsStore
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.ui.island.CutoutRect
import ai.oyi.device.ui.island.placeIsland
import ai.oyi.device.ui.theme.oyiColorScheme
import androidx.compose.ui.unit.DpSize
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import ai.oyi.device.runtime.DeviceRuntime
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandAction
import ai.oyi.device.ui.island.IslandIdleInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate

/**
 * The OYI Dynamic Island over other apps.
 *
 * A small overlay window at the top of the screen hosting the same
 * [DynamicIsland] composable the app shows in its header. It steps aside
 * while OYI's own screen is in front, so there is only ever one.
 *
 * Where the phone has a punch-hole camera, the island is centred on it (see
 * [placeIsland]) so it grows out of the hole like a Dynamic Island, with the
 * user's calibration nudge on top. It re-centres on rotation, fold and
 * unfold.
 *
 * When OYI's accessibility service is on, the window is an accessibility
 * overlay: those sit above the status bar, so the island can cover the
 * camera and take touches there. Otherwise it is a normal app overlay.
 *
 * It follows [DeviceRuntime.island]: chat and live-voice turns (listening,
 * thinking, tools, speaking, done), phone control (step, Stop, Approve),
 * focus nudges and alerts, plus [DeviceRuntime.islandSecondary] as a second
 * bubble. When nothing is going on it rests as a small capsule with the app
 * in front and how long you have been in it.
 */
class OverlayController(
    private val context: Context,
    private val runtime: DeviceRuntime,
    private val usage: UsageRepository,
    private val settings: SettingsStore,
    private val scope: CoroutineScope,
) {

    private var windowManager: WindowManager? = null
    private var root: ComposeView? = null
    private var owner: OverlayLifecycleOwner? = null
    private val jobs = mutableListOf<Job>()
    private var showing = false
    private var params: WindowManager.LayoutParams? = null
    private var viaAccessibility = false

    /**
     * True while the island changes size. A window that wraps its content
     * is relaid out by the system on every frame of a resize, a frame or two
     * behind the content, so the island was cut and jumped as it opened and
     * closed. Instead, for the length of a morph the window takes one fixed,
     * big-enough size (one relayout), the island animates freely inside it,
     * and the window wraps it again once it has settled (one more).
     */
    private val morphing = mutableStateOf(false)
    private var morphJob: Job? = null

    private fun beginMorph() {
        morphJob?.cancel()
        if (!morphing.value) {
            morphing.value = true
            sizeWindow(fixed = true)
        }
        morphJob = scope.launch {
            delay(MORPH_SETTLE_MS)
            morphing.value = false
            sizeWindow(fixed = false)
        }
    }

    private fun sizeWindow(fixed: Boolean) {
        val view = root ?: return
        val layout = params ?: return
        val manager = windowManager ?: return
        val metrics = context.resources.displayMetrics
        if (fixed) {
            // Centred on the island (gravity centre plus the x offset), and
            // narrow enough to stay on screen so the system never shifts it.
            val width = screenWidth(manager, metrics.widthPixels) - 2 * kotlin.math.abs(layout.x)
            layout.width = width.coerceAtLeast((200 * metrics.density).toInt())
            layout.height = (MORPH_HEIGHT_DP * metrics.density).toInt()
        } else {
            layout.width = WindowManager.LayoutParams.WRAP_CONTENT
            layout.height = WindowManager.LayoutParams.WRAP_CONTENT
        }
        runCatching { manager.updateViewLayout(view, layout) }
    }

    /** The camera hole the island keeps clear of, read by the composable. */
    private val cutout = mutableStateOf(DpSize.Zero)

    private val configCallbacks = object : ComponentCallbacks {
        override fun onConfigurationChanged(newConfig: Configuration) = reanchor()

        @Deprecated("Deprecated in Java")
        override fun onLowMemory() = Unit
    }

    fun show() {
        if (showing) return
        val service = OyiAccessibilityService.instance
        if (service == null && !Settings.canDrawOverlays(context)) return

        // An accessibility overlay needs the service's own window manager.
        val host: Context = service ?: context
        val manager = host.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val lifecycle = OverlayLifecycleOwner()
        val view = ComposeView(host).apply {
            setViewCompositionStrategy(ViewCompositionStrategy.DisposeOnDetachedFromWindow)
        }
        lifecycle.attach(view)
        view.setContent { OverlayContent() }

        val layout = buildParams(accessibility = service != null)
        try {
            manager.addView(view, layout)
        } catch (_: Exception) {
            lifecycle.destroy()
            return
        }
        lifecycle.resume()
        windowManager = manager
        root = view
        owner = lifecycle
        params = layout
        viaAccessibility = service != null
        showing = true
        reanchor()
        runCatching { context.registerComponentCallbacks(configCallbacks) }

        // One island at a time: hide the overlay while OYI itself is open.
        jobs += scope.launch {
            runtime.appVisible.collect { appVisible ->
                root?.visibility = if (appVisible) View.GONE else View.VISIBLE
            }
        }
        // A new state from the runtime: make room before it recomposes.
        jobs += scope.launch {
            runtime.island
                .map { it.kind }
                .distinctUntilChanged()
                .drop(1)
                .collect { beginMorph() }
        }
        // Calibration changes move the window at once.
        jobs += scope.launch {
            settings.settings
                .map { it.islandNudgeXDp to it.islandNudgeYDp }
                .distinctUntilChanged()
                .collect { reanchor() }
        }
        // Control access turned on or off: move to the better window type.
        jobs += scope.launch {
            runtime.accessibilityReady
                .drop(1)
                .collect { ready ->
                    if (ready != viaAccessibility) {
                        hide()
                        show()
                    }
                }
        }
    }

    fun hide() {
        morphJob?.cancel()
        morphing.value = false
        val running = jobs.toList()
        jobs.clear()
        running.forEach { it.cancel() }
        runCatching { context.unregisterComponentCallbacks(configCallbacks) }
        val view = root ?: return
        root = null
        showing = false
        try {
            windowManager?.removeView(view)
        } catch (_: Exception) {
            // Already gone.
        }
        windowManager = null
        params = null
        owner?.destroy()
        owner = null
    }

    fun isShowing(): Boolean = showing

    /** Centres the window on the camera (or docks it) and applies the nudge. */
    private fun reanchor() {
        val view = root ?: return
        val layout = params ?: return
        val manager = windowManager ?: return
        val metrics = context.resources.displayMetrics
        val nudge = settings.settings.value
        val placement = placeIsland(
            screenWidthPx = screenWidth(manager, metrics.widthPixels),
            density = metrics.density,
            cutout = topCutout(manager),
            nudgeXDp = nudge.islandNudgeXDp,
            nudgeYDp = nudge.islandNudgeYDp,
            padTopDp = 0,
        )
        cutout.value = DpSize(placement.cutoutWidthDp.dp, placement.cutoutHeightDp.dp)
        layout.x = placement.x
        layout.y = placement.y
        runCatching { manager.updateViewLayout(view, layout) }
    }

    private fun screenWidth(manager: WindowManager, fallback: Int): Int =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            manager.currentWindowMetrics.bounds.width()
        } else {
            fallback
        }

    /** The cutout on the top edge, if the display has one. */
    private fun topCutout(manager: WindowManager): CutoutRect? {
        val rect: Rect? = when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.R ->
                manager.currentWindowMetrics.windowInsets.displayCutout?.boundingRectTop
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ->
                @Suppress("DEPRECATION")
                manager.defaultDisplay?.cutout?.boundingRectTop
            else -> null
        }
        if (rect == null || rect.isEmpty) return null
        return CutoutRect(rect.left, rect.top, rect.right, rect.bottom)
    }

    @androidx.compose.runtime.Composable
    private fun OverlayContent() {
        val island by runtime.island.collectAsStateWithLifecycle()
        val secondary by runtime.islandSecondary.collectAsStateWithLifecycle()
        val level by runtime.voiceLevel.collectAsStateWithLifecycle()
        val foreground by runtime.foreground.collectAsStateWithLifecycle()
        val connection by runtime.connection.collectAsStateWithLifecycle()
        val linkError by runtime.connectionError.collectAsStateWithLifecycle()
        val prefs by settings.settings.collectAsStateWithLifecycle()

        // The resting pill shows the app in front and its session time.
        var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
        var todayMs by remember { mutableStateOf(-1L) }
        LaunchedEffect(foreground?.packageName) {
            while (true) {
                now = System.currentTimeMillis()
                val pkg = foreground?.packageName
                todayMs = if (pkg == null) {
                    -1L
                } else {
                    withContext(Dispatchers.IO) {
                        runCatching { usage.totalFor(LocalDate.now().toString(), pkg) }.getOrDefault(-1L)
                    }
                }
                delay(1_000)
            }
        }

        val idle = IslandIdleInfo(
            appLabel = foreground?.label.orEmpty(),
            sessionMs = foreground?.let { now - it.since } ?: 0L,
            todayMs = todayMs,
            link = connection,
            linkError = linkError,
        )
        val accent = remember(prefs.wallpaperColors) {
            oyiColorScheme(context, prefs.wallpaperColors).primary
        }
        val hole by cutout

        // The theme gives the island OYI's typeface; it draws nothing itself.
        val stacked = secondary != null
        LaunchedEffect(stacked) { beginMorph() }
        val wide by morphing
        ai.oyi.device.ui.theme.OyiTheme(wallpaperColors = prefs.wallpaperColors) {
        // While morphing, the window is a fixed size and the island sits at
        // its top centre, at the same spot on screen as when it wraps it.
        Box(if (wide) Modifier.fillMaxSize() else Modifier, contentAlignment = Alignment.TopCenter) {
        DynamicIsland(
            onMorph = ::beginMorph,
            state = island,
            idle = idle,
            level = level,
            onAction = ::onAction,
            onLongPress = ::openApp,
            secondary = secondary,
            cutout = hole,
            accent = accent,
            // Room for the shadow: the window wraps the island tightly. On a
            // camera the island starts at the window's top edge.
            modifier = Modifier.padding(
                start = 12.dp,
                end = 12.dp,
                top = if (hole.width > 0.dp) 0.dp else 10.dp,
                bottom = 14.dp,
            ),
        )
        }
        }
    }

    private fun onAction(action: IslandAction) {
        when (action) {
            IslandAction.APPROVE -> runtime.resolveApproval(true)
            IslandAction.DENY -> runtime.resolveApproval(false)
            IslandAction.STOP -> runtime.cancelActiveCommand()
            IslandAction.DISMISS -> runtime.dismissIsland()
            IslandAction.OPEN_APP -> openApp()
            IslandAction.INTERRUPT -> runtime.interruptAgent()
            IslandAction.SWAP -> runtime.swapIsland()
        }
    }

    private fun openApp() {
        runCatching {
            context.startActivity(
                Intent(context, MainActivity::class.java).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                }
            )
        }
    }

    private fun buildParams(accessibility: Boolean): WindowManager.LayoutParams {
        val type = when {
            accessibility -> WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.O -> WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
            else -> {
                @Suppress("DEPRECATION")
                WindowManager.LayoutParams.TYPE_PHONE
            }
        }
        // Not focusable: typing and the back button stay with the app below.
        // Touches outside the island's own bounds pass straight through.
        val flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS
        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            flags,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            x = 0
            y = dp(2)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                layoutInDisplayCutoutMode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
                } else {
                    WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
                }
            }
        }
    }

    private fun dp(value: Int): Int =
        (value * context.resources.displayMetrics.density).toInt()

    private companion object {
        /** Long enough for the island's spring to settle, bounce included. */
        const val MORPH_SETTLE_MS = 850L

        /** Tall enough for the biggest card (an approval with its buttons). */
        const val MORPH_HEIGHT_DP = 340
    }
}
