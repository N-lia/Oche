package ai.oyi.device.service

import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.text.TextUtils
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import ai.oyi.device.MainActivity
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.model.IslandState
import ai.oyi.device.runtime.DeviceRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate

/**
 * The OYI Dynamic Island overlay.
 *
 * Docked at the top of the screen like a native Dynamic Island, it morphs
 * between:
 *  - Compact Pill (Idle): status dot + OYI + foreground app + session timer
 *  - Working Pill: live step badge + current UI action + Stop button
 *  - Task Completed Card: emerald check badge + result summary (auto-collapses)
 *  - Approval Required Card: amber badge + agent question + Approve / Deny buttons
 *  - Expanded Telemetry Drawer: tapped from the compact pill
 */
class OverlayController(
    private val context: Context,
    private val runtime: DeviceRuntime,
    private val usage: UsageRepository,
    private val scope: CoroutineScope,
) {

    private val windowManager =
        context.getSystemService(Context.WINDOW_SERVICE) as WindowManager

    private var root: View? = null
    private var islandShell: LinearLayout? = null
    private var shellBackground: GradientDrawable? = null

    // Compact bar views
    private var compactBar: LinearLayout? = null
    private var dotView: View? = null
    private var dotBackground: GradientDrawable? = null
    private var brandText: TextView? = null
    private var compactAppText: TextView? = null
    private var compactTimerText: TextView? = null

    // Notification / active state card views (Working / Completed / Approval / Error)
    private var eventCard: LinearLayout? = null
    private var eventBadge: TextView? = null
    private var eventTitle: TextView? = null
    private var eventSubtitle: TextView? = null
    private var approvalRow: LinearLayout? = null
    private var workingRow: LinearLayout? = null
    private var dismissRow: LinearLayout? = null

    // Expanded telemetry drawer (when user taps the compact pill)
    private var telemetryDrawer: LinearLayout? = null
    private var appValueText: TextView? = null
    private var durationValueText: TextView? = null
    private var todayValueText: TextView? = null
    private var linkValueText: TextView? = null

    private var showing = false
    private var drawerExpanded = false
    private var ticker: Job? = null
    private var islandCollector: Job? = null
    private var autoDismissJob: Job? = null

    private var startX = 0
    private var startY = 0
    private var touchX = 0f
    private var touchY = 0f
    private var dragging = false

    fun show() {
        if (showing) return
        if (!Settings.canDrawOverlays(context)) return

        val view = buildView()
        val params = buildParams()
        try {
            windowManager.addView(view, params)
        } catch (_: Exception) {
            return
        }
        root = view
        showing = true
        startTicker()
        startIslandCollector()
    }

    fun hide() {
        ticker?.cancel()
        ticker = null
        islandCollector?.cancel()
        islandCollector = null
        autoDismissJob?.cancel()
        autoDismissJob = null
        val view = root ?: return
        root = null
        showing = false
        try {
            windowManager.removeView(view)
        } catch (_: Exception) {
            // Already gone.
        }
    }

    fun isShowing(): Boolean = showing

    private fun buildParams(): WindowManager.LayoutParams {
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
        } else {
            @Suppress("DEPRECATION")
            WindowManager.LayoutParams.TYPE_PHONE
        }
        var flags = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            flags = flags or WindowManager.LayoutParams.FLAG_BLUR_BEHIND
        }
        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            flags,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.CENTER_HORIZONTAL
            x = 0
            y = dp(10)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                blurBehindRadius = dp(24)
            }
        }
    }

    private fun buildView(): View {
        val outer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }

        val bg = GradientDrawable(
            GradientDrawable.Orientation.TL_BR,
            intArrayOf(
                Color.parseColor("#CC2A2A30"),
                Color.parseColor("#BF16161A"),
                Color.parseColor("#CC101013"),
            ),
        ).apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(28).toFloat()
            setStroke(dp(1), Color.parseColor("#52FFFFFF"))
        }
        shellBackground = bg

        val shell = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            background = bg
            setPadding(dp(16), dp(9), dp(16), dp(9))
            elevation = dp(14).toFloat()
        }
        islandShell = shell
        shell.setOnTouchListener { _, event -> onIslandTouch(event) }

        // 1. Compact Pill Row
        val compact = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        compactBar = compact

        val dotBg = GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(Color.parseColor("#F08A65"))
        }
        dotBackground = dotBg
        val dot = View(context).apply {
            background = dotBg
        }
        dotView = dot
        compact.addView(dot, LinearLayout.LayoutParams(dp(8), dp(8)).apply {
            marginEnd = dp(8)
        })

        val brand = TextView(context).apply {
            text = "OYI"
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#F8F6F1"))
        }
        brandText = brand
        compact.addView(brand)

        val sep = TextView(context).apply {
            text = "  ·  "
            textSize = 12f
            setTextColor(Color.parseColor("#8A867E"))
        }
        compact.addView(sep)

        val appLabel = TextView(context).apply {
            text = "Ready"
            textSize = 12f
            maxLines = 1
            ellipsize = TextUtils.TruncateAt.END
            maxWidth = dp(130)
            setTextColor(Color.parseColor("#E6E2D8"))
        }
        compactAppText = appLabel
        compact.addView(appLabel)

        val timerLabel = TextView(context).apply {
            text = "0m"
            textSize = 11.5f
            typeface = Typeface.MONOSPACE
            setTextColor(Color.parseColor("#F7EDD0"))
            setPadding(dp(8), 0, 0, 0)
        }
        compactTimerText = timerLabel
        compact.addView(timerLabel)

        shell.addView(compact)

        // 2. Dynamic Island Liquid Glass Orange Notification Card (high contrast)
        val card = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(2), dp(8), dp(2), dp(4))
            visibility = View.GONE
        }
        eventCard = card

        val badge = TextView(context).apply {
            text = "TASK COMPLETED"
            textSize = 10f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#140D0A"))
            letterSpacing = 0.08f
        }
        eventBadge = badge
        card.addView(badge)

        val titleView = TextView(context).apply {
            text = ""
            textSize = 15.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#0A0807"))
            setPadding(0, dp(2), 0, dp(3))
        }
        eventTitle = titleView
        card.addView(titleView)

        val subtitleView = TextView(context).apply {
            text = ""
            textSize = 13f
            maxLines = 3
            ellipsize = TextUtils.TruncateAt.END
            setTextColor(Color.parseColor("#17100C"))
            setPadding(0, 0, 0, dp(10))
        }
        eventSubtitle = subtitleView
        card.addView(subtitleView)

        // Approval buttons row (Approve / Deny)
        val approvals = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            visibility = View.GONE
        }
        approvalRow = approvals

        val denyBtn = actionPill(
            label = "Deny",
            bgHex = "#330A0807",
            strokeHex = "#4DFFFFFF",
            textHex = "#0A0807",
        ) {
            runtime.resolveApproval(false)
        }
        val approveBtn = actionPill(
            label = "Approve",
            bgHex = "#EB0B0A09",
            strokeHex = "#66FFFFFF",
            textHex = "#FFF8E7",
        ) {
            runtime.resolveApproval(true)
        }
        approvals.addView(denyBtn, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { marginEnd = dp(8) })
        approvals.addView(approveBtn)
        card.addView(approvals)

        // Working row (Stop button)
        val working = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            visibility = View.GONE
        }
        workingRow = working
        val stopBtn = actionPill(
            label = "Stop",
            bgHex = "#EB0B0A09",
            strokeHex = "#66FFFFFF",
            textHex = "#FFF8E7",
        ) {
            runtime.cancelActiveCommand()
        }
        working.addView(stopBtn)
        card.addView(working)

        // Completed / Error dismiss row
        val dismiss = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            visibility = View.GONE
        }
        dismissRow = dismiss
        val dismissBtn = actionPill(
            label = "Done",
            bgHex = "#EB0B0A09",
            strokeHex = "#66FFFFFF",
            textHex = "#FFF8E7",
        ) {
            runtime.dismissIsland()
        }
        dismiss.addView(dismissBtn)
        card.addView(dismiss)

        shell.addView(card, LinearLayout.LayoutParams(dp(292), LinearLayout.LayoutParams.WRAP_CONTENT))

        // 3. Expanded Telemetry Drawer (when compact pill is tapped)
        val drawer = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(2), dp(8), dp(2), dp(4))
            visibility = View.GONE
        }
        telemetryDrawer = drawer

        appValueText = addRow(drawer, "Foreground App", "—")
        durationValueText = addRow(drawer, "Current Session", "0m")
        todayValueText = addRow(drawer, "Screen Time Today", "—")
        linkValueText = addRow(drawer, "Gateway Link", "Offline")

        val drawerActions = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            setPadding(0, dp(8), 0, 0)
        }
        val openAppBtn = actionPill(
            label = "Open OYI",
            bgHex = "#F4E8C1",
            strokeHex = "#F4E8C1",
            textHex = "#181612",
        ) {
            drawerExpanded = false
            drawer.visibility = View.GONE
            runCatching {
                context.startActivity(
                    Intent(context, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                    }
                )
            }
        }
        val hideDrawerBtn = actionPill(
            label = "Close",
            bgHex = "#242424",
            strokeHex = "#333333",
            textHex = "#D6D2C9",
        ) {
            drawerExpanded = false
            drawer.visibility = View.GONE
        }
        drawerActions.addView(openAppBtn, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT,
        ).apply { marginEnd = dp(8) })
        drawerActions.addView(hideDrawerBtn)
        drawer.addView(drawerActions)

        shell.addView(drawer, LinearLayout.LayoutParams(dp(268), LinearLayout.LayoutParams.WRAP_CONTENT))
        outer.addView(shell)
        return outer
    }

    private fun addRow(parent: LinearLayout, label: String, value: String): TextView {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(2), 0, dp(2))
        }
        val labelView = TextView(context).apply {
            text = label
            textSize = 12f
            setTextColor(Color.parseColor("#94A3B8"))
        }
        row.addView(
            labelView,
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        val valueView = TextView(context).apply {
            text = value
            textSize = 12f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor("#F8FAFC"))
            gravity = Gravity.END
        }
        row.addView(valueView)
        parent.addView(
            row,
            LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT,
            ),
        )
        return valueView
    }

    private fun actionPill(
        label: String,
        bgHex: String,
        strokeHex: String,
        textHex: String,
        onClick: () -> Unit,
    ): TextView =
        TextView(context).apply {
            text = label
            textSize = 11.5f
            typeface = Typeface.DEFAULT_BOLD
            setTextColor(Color.parseColor(textHex))
            gravity = Gravity.CENTER
            setPadding(dp(12), dp(6), dp(12), dp(6))
            background = GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = dp(16).toFloat()
                setColor(Color.parseColor(bgHex))
                setStroke(dp(1), Color.parseColor(strokeHex))
            }
            setOnClickListener { onClick() }
        }

    private fun onIslandTouch(event: MotionEvent): Boolean {
        val params = root?.layoutParams as? WindowManager.LayoutParams ?: return false
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                startX = params.x
                startY = params.y
                touchX = event.rawX
                touchY = event.rawY
                dragging = false
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val deltaX = (event.rawX - touchX).toInt()
                val deltaY = (event.rawY - touchY).toInt()
                if (kotlin.math.abs(deltaX) > TOUCH_SLOP || kotlin.math.abs(deltaY) > TOUCH_SLOP) {
                    dragging = true
                }
                params.x = startX + deltaX
                params.y = startY + deltaY
                root?.let { view ->
                    try {
                        windowManager.updateViewLayout(view, params)
                    } catch (_: Exception) {
                    }
                }
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (!dragging) {
                    val island = runtime.island.value
                    if (island.kind == IslandKind.IDLE) {
                        drawerExpanded = !drawerExpanded
                        telemetryDrawer?.visibility = if (drawerExpanded) View.VISIBLE else View.GONE
                    } else if (island.kind == IslandKind.COMPLETED || island.kind == IslandKind.ERROR) {
                        runtime.dismissIsland()
                    }
                }
                return true
            }
        }
        return false
    }

    private fun startIslandCollector() {
        islandCollector?.cancel()
        islandCollector = scope.launch {
            runtime.island.collect { state ->
                renderIslandState(state)
            }
        }
    }

    private fun setCompactTextTone(onOrangeSurface: Boolean) {
        if (onOrangeSurface) {
            brandText?.setTextColor(Color.parseColor("#0A0807"))
            compactAppText?.setTextColor(Color.parseColor("#1C120D"))
            compactTimerText?.setTextColor(Color.parseColor("#0A0807"))
            dotBackground?.setColor(Color.parseColor("#0A0807"))
        } else {
            brandText?.setTextColor(Color.parseColor("#F8F6F1"))
            compactAppText?.setTextColor(Color.parseColor("#E6E2D8"))
            compactTimerText?.setTextColor(Color.parseColor("#F7EDD0"))
            dotBackground?.setColor(Color.parseColor("#F08A65"))
        }
    }

    private fun applyLiquidGlassSurface(activeOrange: Boolean, highlightRimHex: String) {
        val colors = if (activeOrange) {
            intArrayOf(
                Color.parseColor("#F5FFA685"),
                Color.parseColor("#F2F08A65"),
                Color.parseColor("#EEE07450"),
            )
        } else {
            intArrayOf(
                Color.parseColor("#CC2A2A30"),
                Color.parseColor("#BF16161A"),
                Color.parseColor("#CC101013"),
            )
        }
        shellBackground?.colors = colors
        shellBackground?.setStroke(dp(1), Color.parseColor(highlightRimHex))
        shellBackground?.cornerRadius = dp(if (activeOrange) 26 else 28).toFloat()
    }

    private fun renderIslandState(state: IslandState) {
        autoDismissJob?.cancel()
        when (state.kind) {
            IslandKind.IDLE -> {
                eventCard?.visibility = View.GONE
                telemetryDrawer?.visibility = if (drawerExpanded) View.VISIBLE else View.GONE
                applyLiquidGlassSurface(activeOrange = false, highlightRimHex = "#52FFFFFF")
                setCompactTextTone(false)
            }
            IslandKind.WORKING -> {
                drawerExpanded = false
                telemetryDrawer?.visibility = View.GONE
                eventCard?.visibility = View.VISIBLE
                eventBadge?.text = if (state.step > 0) "AGENT WORKING · STEP ${state.step}" else "AGENT WORKING"
                eventTitle?.text = state.title.ifBlank { "Controlling Phone" }
                eventSubtitle?.text = state.subtitle.ifBlank { "Executing next action…" }
                approvalRow?.visibility = View.GONE
                workingRow?.visibility = View.VISIBLE
                dismissRow?.visibility = View.GONE
                applyLiquidGlassSurface(activeOrange = true, highlightRimHex = "#99FFF8F2")
                setCompactTextTone(true)
            }
            IslandKind.APPROVAL_REQUIRED -> {
                drawerExpanded = false
                telemetryDrawer?.visibility = View.GONE
                eventCard?.visibility = View.VISIBLE
                eventBadge?.text = "APPROVAL REQUIRED"
                eventTitle?.text = state.title.ifBlank { "Confirm Action" }
                eventSubtitle?.text = state.subtitle.ifBlank { "OYI needs your permission to proceed." }
                approvalRow?.visibility = View.VISIBLE
                workingRow?.visibility = View.GONE
                dismissRow?.visibility = View.GONE
                applyLiquidGlassSurface(activeOrange = true, highlightRimHex = "#CCFFF8E7")
                setCompactTextTone(true)
            }
            IslandKind.COMPLETED -> {
                drawerExpanded = false
                telemetryDrawer?.visibility = View.GONE
                eventCard?.visibility = View.VISIBLE
                eventBadge?.text = "TASK COMPLETED"
                eventTitle?.text = state.title.ifBlank { "Done" }
                eventSubtitle?.text = state.subtitle
                approvalRow?.visibility = View.GONE
                workingRow?.visibility = View.GONE
                dismissRow?.visibility = View.VISIBLE
                applyLiquidGlassSurface(activeOrange = true, highlightRimHex = "#99FFF8F2")
                setCompactTextTone(true)
                scheduleAutoDismiss(state.updatedAt, 6_500L)
            }
            IslandKind.ERROR -> {
                drawerExpanded = false
                telemetryDrawer?.visibility = View.GONE
                eventCard?.visibility = View.VISIBLE
                eventBadge?.text = "ATTENTION"
                eventTitle?.text = state.title.ifBlank { "Task Stopped" }
                eventSubtitle?.text = state.subtitle
                approvalRow?.visibility = View.GONE
                workingRow?.visibility = View.GONE
                dismissRow?.visibility = View.VISIBLE
                applyLiquidGlassSurface(activeOrange = true, highlightRimHex = "#99FFF8F2")
                setCompactTextTone(true)
                scheduleAutoDismiss(state.updatedAt, 6_500L)
            }
        }
    }

    private fun scheduleAutoDismiss(stamp: Long, delayMs: Long) {
        autoDismissJob = scope.launch {
            delay(delayMs)
            if (runtime.island.value.updatedAt == stamp) {
                runtime.dismissIsland()
            }
        }
    }

    private fun startTicker() {
        ticker?.cancel()
        ticker = scope.launch {
            while (true) {
                update()
                delay(1000)
            }
        }
    }

    private suspend fun update() {
        val foreground = runtime.foreground.value
        val appLabel = foreground?.label ?: "Idle"
        val elapsedStr = foreground?.let { formatDuration(System.currentTimeMillis() - it.since) } ?: "0m"

        compactAppText?.text = appLabel
        compactTimerText?.text = elapsedStr
        appValueText?.text = appLabel
        durationValueText?.text = elapsedStr

        val connection = runtime.connection.value
        val error = runtime.connectionError.value
        val (statusLabel, dotHex) = when (connection) {
            ConnectionState.CONNECTED -> (if (runtime.busy.value) "Active" else "Connected") to "#DE7E5D"
            ConnectionState.CONNECTING -> "Connecting" to "#F4E8C1"
            ConnectionState.ERROR -> error.ifBlank { "Error" } to "#DE7E5D"
            ConnectionState.DISCONNECTED -> error.ifBlank { "Offline" } to "#6E6A63"
        }
        linkValueText?.text = statusLabel
        if (runtime.island.value.kind == IslandKind.IDLE) {
            dotBackground?.setColor(Color.parseColor(dotHex))
        }

        val packageName = foreground?.packageName
        if (packageName != null) {
            val total = withContext(Dispatchers.IO) {
                usage.totalFor(LocalDate.now().toString(), packageName)
            }
            todayValueText?.text = formatDuration(total)
        } else {
            todayValueText?.text = "—"
        }
    }

    private fun formatDuration(millis: Long): String {
        val totalSeconds = (millis / 1000).coerceAtLeast(0)
        val totalMinutes = totalSeconds / 60
        val hours = totalMinutes / 60
        val minutes = totalMinutes % 60
        val seconds = totalSeconds % 60
        return when {
            hours > 0 -> "${hours}h ${minutes}m"
            minutes > 0 -> "${minutes}m"
            else -> "${seconds}s"
        }
    }

    private fun dp(value: Int): Int =
        (value * context.resources.displayMetrics.density).toInt()

    private companion object {
        const val TOUCH_SLOP = 8
    }
}
