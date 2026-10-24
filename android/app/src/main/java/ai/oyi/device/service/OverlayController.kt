package ai.oyi.device.service

import android.content.Context
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.provider.Settings
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView
import ai.oyi.device.data.UsageRepository
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.runtime.DeviceRuntime
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.LocalDate

/**
 * The always-on OYI bubble.
 *
 * It shows the current app, how long it has been open, the link state to the
 * OYI gateway, and today's total for that app.  Tapping the bubble expands a
 * small panel; dragging it moves it.
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
    private var bubble: TextView? = null
    private var panel: LinearLayout? = null
    private var appText: TextView? = null
    private var durationText: TextView? = null
    private var statusText: TextView? = null
    private var totalText: TextView? = null

    private var showing = false
    private var ticker: Job? = null

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
    }

    fun hide() {
        ticker?.cancel()
        ticker = null
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
        return WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            type,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = dp(12)
            y = dp(120)
        }
    }

    private fun buildView(): View {
        val container = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
        }

        val bubbleView = TextView(context).apply {
            text = "O"
            textSize = 20f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                shape = GradientDrawable.OVAL
                setColor(Color.parseColor("#CC7C5CFF"))
                setStroke(dp(2), Color.parseColor("#66FFFFFF"))
            }
            contentDescription = "OYI"
        }
        bubble = bubbleView
        bubbleView.setOnTouchListener { _, event -> onBubbleTouch(event) }
        container.addView(
            bubbleView,
            LinearLayout.LayoutParams(dp(56), dp(56)),
        )

        val panelView = LinearLayout(context).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(12), dp(16), dp(12))
            background = GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = dp(18).toFloat()
                setColor(Color.parseColor("#F21B1830"))
                setStroke(dp(1), Color.parseColor("#33FFFFFF"))
            }
            visibility = View.GONE
        }
        panel = panelView

        val title = TextView(context).apply {
            text = "OYI"
            textSize = 13f
            setTextColor(Color.parseColor("#B3FFFFFF"))
        }
        panelView.addView(title)

        appText = addRow(panelView, "App", "—")
        durationText = addRow(panelView, "Time", "0m")
        totalText = addRow(panelView, "Today", "—")
        statusText = addRow(panelView, "Link", "Offline")

        val actions = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
        }
        val hideButton = pillButton("Hide") {
            panelView.visibility = View.GONE
        }
        actions.addView(hideButton)
        panelView.addView(actions)

        container.addView(
            panelView,
            LinearLayout.LayoutParams(dp(240), LinearLayout.LayoutParams.WRAP_CONTENT).apply {
                topMargin = dp(8)
            },
        )

        return container
    }

    private fun addRow(parent: LinearLayout, label: String, value: String): TextView {
        val row = LinearLayout(context).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val labelView = TextView(context).apply {
            text = label
            textSize = 13f
            setTextColor(Color.parseColor("#99FFFFFF"))
        }
        row.addView(
            labelView,
            LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f),
        )
        val valueView = TextView(context).apply {
            text = value
            textSize = 13f
            setTextColor(Color.WHITE)
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

    private fun pillButton(label: String, onClick: () -> Unit): TextView =
        TextView(context).apply {
            text = label
            textSize = 12f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setPadding(dp(14), dp(6), dp(14), dp(6))
            background = GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = dp(14).toFloat()
                setColor(Color.parseColor("#33FFFFFF"))
            }
            setOnClickListener { onClick() }
        }

    private fun onBubbleTouch(event: MotionEvent): Boolean {
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
                        // The view may have been removed while dragging.
                    }
                }
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                if (!dragging) {
                    panel?.visibility =
                        if (panel?.visibility == View.VISIBLE) View.GONE else View.VISIBLE
                }
                return true
            }
        }
        return false
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
        appText?.text = foreground?.label ?: "—"
        durationText?.text = foreground?.let { formatDuration(System.currentTimeMillis() - it.since) } ?: "0m"

        val connection = runtime.connection.value
        val error = runtime.connectionError.value
        statusText?.text = when (connection) {
            ConnectionState.CONNECTED -> if (runtime.busy.value) "Controlling" else "Connected"
            ConnectionState.CONNECTING -> "Connecting"
            ConnectionState.ERROR -> error.ifBlank { "Error" }
            ConnectionState.DISCONNECTED -> error.ifBlank { "Offline" }
        }

        val packageName = foreground?.packageName
        if (packageName != null) {
            val total = withContext(Dispatchers.IO) {
                usage.totalFor(LocalDate.now().toString(), packageName)
            }
            totalText?.text = formatDuration(total)
        } else {
            totalText?.text = "—"
        }
    }

    private fun formatDuration(millis: Long): String {
        val totalMinutes = millis / 60_000
        val hours = totalMinutes / 60
        val minutes = totalMinutes % 60
        return when {
            hours > 0 -> "${hours}h ${minutes}m"
            minutes > 0 -> "${minutes}m"
            else -> "0m"
        }
    }

    private fun dp(value: Int): Int =
        (value * context.resources.displayMetrics.density).toInt()

    private companion object {
        const val TOUCH_SLOP = 8
    }
}
