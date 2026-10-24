package ai.oyi.device.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.GestureDescription.Builder
import android.accessibilityservice.GestureDescription.StrokeDescription
import android.graphics.Bitmap
import android.graphics.Rect
import android.os.Bundle
import android.util.Base64
import android.view.Display
import android.view.accessibility.AccessibilityNodeInfo
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
    val tree: JSONArray,
    val screenshot: String,
    val width: Int,
    val height: Int,
)

/**
 * Executes UI actions on the phone.
 *
 * Every action runs on the main thread because accessibility APIs must be
 * called from the UI thread.
 */
class DeviceController(private val service: AccessibilityService) {

    suspend fun snapshot(): ScreenSnapshot? = withContext(Dispatchers.Main) {
        val root = service.rootInActiveWindow
        val metrics = service.resources.displayMetrics
        val tree = UiTreeSerializer.serialize(root)
        val packageName = root?.packageName?.toString().orEmpty()
        val bitmap = captureScreenshot()
        ScreenSnapshot(
            packageName = packageName,
            tree = tree,
            screenshot = bitmap?.toJpegBase64().orEmpty(),
            width = metrics.widthPixels,
            height = metrics.heightPixels,
        )
    }

    suspend fun execute(action: JSONObject): Boolean = withContext(Dispatchers.Main) {
        when (action.optString("type")) {
            "tap" -> tap(action.optInt("x", 500), action.optInt("y", 500))
            "tap_text" -> tapText(action.optString("text"))
            "type" -> typeText(action.optString("text"))
            "scroll" -> scroll(
                action.optString("direction", "down"),
                action.optDouble("distance", 0.6).toFloat(),
            )
            "back" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            "home" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            "recents" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_RECENTS)
            "open_app" -> openApp(action.optString("package"))
            "wait" -> {
                delay((action.optDouble("seconds", 1.0) * 1000).toLong())
                true
            }
            else -> false
        }
    }

    // ---------------------------------------------------------------- taps

    private fun tap(xNormalized: Int, yNormalized: Int): Boolean {
        val metrics = service.resources.displayMetrics
        val x = (xNormalized.coerceIn(0, 1000) / 1000f) * metrics.widthPixels
        val y = (yNormalized.coerceIn(0, 1000) / 1000f) * metrics.heightPixels
        val path = android.graphics.Path().apply { moveTo(x, y) }
        val gesture = Builder()
            .addStroke(StrokeDescription(path, 0, 80))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    private fun tapText(text: String): Boolean {
        val target = text.trim()
        if (target.isEmpty()) return false
        val node = findNode(target) ?: return false
        if (node.isClickable && node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return true
        }
        val clickable = node.parent?.takeIf { it.isClickable }
        if (clickable != null && clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
            return true
        }
        val rect = Rect()
        node.getBoundsInScreen(rect)
        if (rect.width() <= 0 || rect.height() <= 0) return false
        return tapAtPixels(rect.centerX().toFloat(), rect.centerY().toFloat())
    }

    private fun tapAtPixels(x: Float, y: Float): Boolean {
        val path = android.graphics.Path().apply { moveTo(x, y) }
        val gesture = Builder()
            .addStroke(StrokeDescription(path, 0, 80))
            .build()
        return service.dispatchGesture(gesture, null, null)
    }

    // --------------------------------------------------------------- typing

    private fun typeText(text: String): Boolean {
        if (text.isEmpty()) return false
        var node = service.rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (node == null) {
            node = findFirstEditable()
            if (node != null) {
                node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            }
        }
        val target = node ?: return false
        val arguments = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        return target.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    }

    // -------------------------------------------------------------- scrolling

    private fun scroll(direction: String, distance: Float): Boolean {
        val forward = direction == "down" || direction == "right"
        val node = findScrollable()?.also { scrollTarget = it }
        if (node != null) {
            val action = if (forward) {
                AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            } else {
                AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            }
            if (node.performAction(action)) return true
        }
        return swipe(direction, distance)
    }

    private fun swipe(direction: String, distance: Float): Boolean {
        val metrics = service.resources.displayMetrics
        val width = metrics.widthPixels.toFloat()
        val height = metrics.heightPixels.toFloat()
        val amount = distance.coerceIn(0.1f, 1f)
        val path = android.graphics.Path()
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

    private var scrollTarget: AccessibilityNodeInfo? = null

    private fun findScrollable(): AccessibilityNodeInfo? {
        scrollTarget = null
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

    private fun findNode(text: String): AccessibilityNodeInfo? {
        val needle = text.lowercase()
        val root = service.rootInActiveWindow ?: return null
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited++
            val label = node.text?.toString()?.lowercase()
            val description = node.contentDescription?.toString()?.lowercase()
            val id = node.viewIdResourceName?.lowercase()
            if (label?.contains(needle) == true ||
                description?.contains(needle) == true ||
                id?.contains(needle) == true
            ) {
                return node
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }
        return null
    }

    private fun openApp(packageName: String): Boolean {
        if (packageName.isBlank()) return false
        val intent = service.packageManager.getLaunchIntentForPackage(packageName) ?: return false
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        service.startActivity(intent)
        return true
    }

    private suspend fun captureScreenshot(): Bitmap? =
        suspendCancellableCoroutine { continuation ->
            if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.R) {
                continuation.resume(null)
                return@suspendCancellableCoroutine
            }
            service.takeScreenshot(
                Display.DEFAULT_DISPLAY,
                service.mainExecutor,
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
