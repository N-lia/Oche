package ai.oyi.device.accessibility

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.graphics.Path
import android.graphics.Rect
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.GestureDescription.Builder
import android.accessibilityservice.GestureDescription.StrokeDescription
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume

/** A screen snapshot sent to the vision planner. */
data class ScreenSnapshot(
    val packageName: String,
    val elements: List<ScreenElement>,
    val elementJson: JSONArray,
    val elementText: String,
    val screenshot: String,
    val width: Int,
    val height: Int,
)

/** Outcome of one action, including why it was refused. */
data class ExecutionResult(
    val ok: Boolean,
    val message: String = "",
)

/**
 * Executes UI actions on the phone.
 *
 * Every action runs on the main thread because accessibility APIs must be
 * called from the UI thread.
 *
 * Targets are resolved dynamically first: an element index from the current
 * screen index is executed against the live node it refers to. Raw coordinates
 * are the fallback for custom-drawn UI that exposes no accessible element.
 */
class DeviceController(private val service: AccessibilityService) {

    /** The elements of the most recent snapshot, used to resolve indices. */
    private var currentElements: List<ScreenElement> = emptyList()

    suspend fun snapshot(): ScreenSnapshot = withContext(Dispatchers.Main) {
        val root = service.rootInActiveWindow
        val metrics = service.resources.displayMetrics
        val width = metrics.widthPixels
        val height = metrics.heightPixels

        val elements = ScreenElements.collect(root, width, height)
        currentElements = elements

        val bitmap = captureScreenshot()
        ScreenSnapshot(
            packageName = root?.packageName?.toString().orEmpty(),
            elements = elements,
            elementJson = ScreenElements.toJson(elements),
            elementText = ScreenElements.format(elements, width, height),
            screenshot = bitmap?.toJpegBase64().orEmpty(),
            width = width,
            height = height,
        )
    }

    suspend fun execute(action: JSONObject): ExecutionResult = withContext(Dispatchers.Main) {
        when (action.optString("type")) {
            "tap" -> tap(action)
            "type" -> typeText(action)
            "scroll" -> scroll(
                action.optString("direction", "down"),
                action.optDouble("distance", 0.6).toFloat(),
                action.optInt("target", -1).takeIf { it > 0 },
            )
            "back" -> global(AccessibilityService.GLOBAL_ACTION_BACK, "back")
            "home" -> global(AccessibilityService.GLOBAL_ACTION_HOME, "home")
            "recents" -> global(AccessibilityService.GLOBAL_ACTION_RECENTS, "recents")
            "open_app" -> openApp(action.optString("package"))
            "wait" -> {
                delay((action.optDouble("seconds", 1.0) * 1000).toLong())
                ExecutionResult(true)
            }
            else -> ExecutionResult(false, "Unsupported action '${action.optString("type")}'")
        }
    }

    // ---------------------------------------------------------------- taps

    private fun tap(action: JSONObject): ExecutionResult {
        val target = action.optInt("target", -1).takeIf { it > 0 }
        if (target != null) {
            val element = currentElements.firstOrNull { it.index == target }
            if (element == null) {
                return ExecutionResult(
                    false,
                    "Element $target is no longer on screen. Take a new look before acting.",
                )
            }
            // Dynamic-first: ask the node itself to act.
            if (element.node.isClickable && element.node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return ExecutionResult(true, "Clicked '${element.label}'")
            }
            val parent = element.node.parent?.takeIf { it.isClickable }
            if (parent != null && parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return ExecutionResult(true, "Clicked '${element.label}'")
            }
            // Coordinate fallback for the same element.
            return tapAtPixels(element.centerX.toFloat(), element.centerY.toFloat())
                .let { ok ->
                    if (ok) ExecutionResult(true, "Tapped '${element.label}'")
                    else ExecutionResult(false, "Could not tap '${element.label}'")
                }
        }

        if (action.has("x") && action.has("y")) {
            return tapAtPixels(
                (action.optInt("x") / 1000f) * service.resources.displayMetrics.widthPixels,
                (action.optInt("y") / 1000f) * service.resources.displayMetrics.heightPixels,
            ).let { ok ->
                if (ok) ExecutionResult(true, "Tapped coordinates")
                else ExecutionResult(false, "Could not tap the coordinates")
            }
        }
        return ExecutionResult(false, "No target given: use an element index or x/y")
    }

    private fun tapAtPixels(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = Builder()
            .addStroke(StrokeDescription(path, 0, 80))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    // --------------------------------------------------------------- typing

    private fun typeText(action: JSONObject): ExecutionResult {
        val text = action.optString("text")
        if (text.isEmpty()) return ExecutionResult(false, "No text to type")

        val target = action.optInt("target", -1).takeIf { it > 0 }
        val node: AccessibilityNodeInfo = when {
            target != null -> {
                val element = currentElements.firstOrNull { it.index == target }
                if (element == null) {
                    return ExecutionResult(
                        false,
                        "Element $target is no longer on screen. Take a new look before acting.",
                    )
                }
                element.node
            }
            else -> service.rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                ?: findFirstEditable()
        } ?: return ExecutionResult(false, "No text field is focused")

        if (!node.isEditable) {
            node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        }
        val arguments = android.os.Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        return if (ok) {
            ExecutionResult(true, "Typed the text")
        } else {
            ExecutionResult(false, "The text field refused the input")
        }
    }

    // -------------------------------------------------------------- scrolling

    private fun scroll(direction: String, distance: Float, target: Int?): ExecutionResult {
        val forward = direction == "down" || direction == "right"
        val node = if (target != null) {
            currentElements.firstOrNull { it.index == target }?.node
        } else {
            findScrollable()
        }
        if (node != null) {
            val action = if (forward) {
                AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            } else {
                AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            }
            if (node.performAction(action)) {
                return ExecutionResult(true, "Scrolled $direction")
            }
        }
        return swipe(direction, distance).let { ok ->
            if (ok) ExecutionResult(true, "Swiped $direction")
            else ExecutionResult(false, "Could not scroll")
        }
    }

    private fun swipe(direction: String, distance: Float): Boolean {
        val metrics = service.resources.displayMetrics
        val width = metrics.widthPixels.toFloat()
        val height = metrics.heightPixels.toFloat()
        val amount = distance.coerceIn(0.1f, 1f)
        val path = Path()
        when (direction) {
            "up" -> {
                val startY = height * 0.75f
                path.moveTo(width / 2f, startY)
                path.lineTo(width / 2f, startY - height * 0.4f * amount)
            }
            "down" -> {
                val startY = height * 0.3f
                path.moveTo(width / 2f, startY)
                path.lineTo(width / 2f, startY + height * 0.4f * amount)
            }
            "left" -> {
                val startX = width * 0.8f
                path.moveTo(startX, height / 2f)
                path.lineTo(startX - width * 0.5f * amount, height / 2f)
            }
            else -> {
                val startX = width * 0.2f
                path.moveTo(startX, height / 2f)
                path.lineTo(startX + width * 0.5f * amount, height / 2f)
            }
        }
        val gesture = Builder().addStroke(StrokeDescription(path, 0, 300)).build()
        return service.dispatchGesture(gesture, null, null)
    }

    // --------------------------------------------------------------- helpers

    private fun global(action: Int, name: String): ExecutionResult =
        if (service.performGlobalAction(action)) {
            ExecutionResult(true, "Pressed $name")
        } else {
            ExecutionResult(false, "Could not press $name")
        }

    private fun openApp(packageName: String): ExecutionResult {
        if (packageName.isBlank()) return ExecutionResult(false, "No package given")
        val intent = service.packageManager.getLaunchIntentForPackage(packageName)
            ?: return ExecutionResult(false, "$packageName is not installed")
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        service.startActivity(intent)
        return ExecutionResult(true, "Opened $packageName")
    }

    private fun findScrollable(): AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited++
            if (node.isScrollable) return node
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }
        return null
    }

    private fun findFirstEditable(): AccessibilityNodeInfo? {
        val root = service.rootInActiveWindow ?: return null
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited++
            if (node.isEditable) return node
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }
        return null
    }

    private suspend fun captureScreenshot(): Bitmap? =
        suspendCancellableCoroutine { continuation ->
            if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.R) {
                continuation.resume(null)
                return@suspendCancellableCoroutine
            }
            service.takeScreenshot(
                Display.DEFAULT_DISPLAY,
                androidx.core.content.ContextCompat.getMainExecutor(service),
                object : AccessibilityService.TakeScreenshotCallback {
                    override fun onSuccess(screenshot: AccessibilityService.ScreenshotResult) {
                        val bitmap = try {
                            val hardware = screenshot.hardwareBuffer
                            val wrapped = Bitmap.wrapHardwareBuffer(
                                hardware,
                                screenshot.colorSpace,
                            )
                            wrapped?.copy(Bitmap.Config.ARGB_8888, false)
                        } catch (_: Exception) {
                            null
                        }
                        continuation.resume(bitmap)
                    }

                    override fun onFailure(errorCode: Int) {
                        continuation.resume(null)
                    }
                },
            )
        }

    private fun Bitmap.toJpegBase64(): String {
        val scaled = if (maxOf(width, height) > MAX_SCREEN_EDGE) {
            val ratio = MAX_SCREEN_EDGE.toFloat() / maxOf(width, height)
            Bitmap.createScaledBitmap(
                this,
                (width * ratio).toInt(),
                (height * ratio).toInt(),
                true,
            )
        } else {
            this
        }
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, out)
        if (scaled !== this) scaled.recycle()
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    private companion object {
        const val MAX_NODES = 2000
        const val MAX_SCREEN_EDGE = 1280
        const val JPEG_QUALITY = 60
    }
}
