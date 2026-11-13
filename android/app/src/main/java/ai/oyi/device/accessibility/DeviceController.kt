package ai.oyi.device.accessibility

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.GestureDescription.Builder
import android.accessibilityservice.GestureDescription.StrokeDescription
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Path
import android.os.Build
import android.os.Bundle
import android.util.Base64
import android.view.Display
import android.view.WindowManager
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
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
 * Targets are resolved dynamically first: an element index or label from the
 * current screen index is executed against the live node it refers to. Raw
 * coordinates are the fallback when no listed element matches or for
 * custom-drawn UI that exposes no accessible element.
 */
class DeviceController(private val service: AccessibilityService) {

    /** The elements of the most recent snapshot, used to resolve indices. */
    private var currentElements: List<ScreenElement> = emptyList()

    private fun screenDimensions(): Pair<Int, Int> {
        val wm = service.getSystemService(Context.WINDOW_SERVICE) as? WindowManager
        if (wm != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = runCatching { wm.currentWindowMetrics.bounds }.getOrNull()
            if (bounds != null && bounds.width() > 0 && bounds.height() > 0) {
                return bounds.width() to bounds.height()
            }
        }
        val metrics = service.resources.displayMetrics
        return metrics.widthPixels to metrics.heightPixels
    }

    private fun collectActiveRoots(): Pair<String, List<AccessibilityNodeInfo>> {
        val selfPkg = service.packageName
        val roots = mutableListOf<AccessibilityNodeInfo>()
        var primaryPackage = ""

        val windows = runCatching { service.windows }.getOrNull().orEmpty()
        if (windows.isNotEmpty()) {
            val appAndDialogWindows = windows.filter { win ->
                val pkg = win.root?.packageName?.toString().orEmpty()
                // OYI's own bubble is a TYPE_APPLICATION_OVERLAY window, which
                // accessibility reports as TYPE_SYSTEM.
                val isOyiOverlay = pkg == selfPkg && win.type != AccessibilityWindowInfo.TYPE_APPLICATION
                !isOyiOverlay && (
                    win.type == AccessibilityWindowInfo.TYPE_APPLICATION ||
                        win.type == AccessibilityWindowInfo.TYPE_SYSTEM
                    )
            }
            for (win in appAndDialogWindows) {
                val root = win.root ?: continue
                val pkg = root.packageName?.toString().orEmpty()
                if (primaryPackage.isEmpty() && win.type == AccessibilityWindowInfo.TYPE_APPLICATION && pkg != "com.android.systemui") {
                    primaryPackage = pkg
                }
                roots.add(root)
            }
        }

        val activeRoot = service.rootInActiveWindow
        if (activeRoot != null) {
            val activePkg = activeRoot.packageName?.toString().orEmpty()
            if (primaryPackage.isEmpty() && activePkg.isNotEmpty()) {
                primaryPackage = activePkg
            }
            if (roots.none { it == activeRoot }) {
                roots.add(0, activeRoot)
            }
        }

        return primaryPackage to roots
    }

    suspend fun snapshot(): ScreenSnapshot = withContext(Dispatchers.Main) {
        val (width, height) = screenDimensions()
        val (packageName, roots) = collectActiveRoots()

        val elements = ScreenElements.collectRoots(roots, width, height)
        currentElements = elements

        val bitmap = captureScreenshot()
        ScreenSnapshot(
            packageName = packageName,
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
            "tap" -> tap(action, longPress = false)
            "long_press" -> tap(action, longPress = true)
            "type" -> typeText(action)
            "press_enter" -> pressEnter()
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
                ExecutionResult(true, "Waited")
            }
            else -> ExecutionResult(false, "Unsupported action '${action.optString("type")}'")
        }
    }

    // ---------------------------------------------------------------- taps

    private suspend fun tap(action: JSONObject, longPress: Boolean): ExecutionResult {
        val target = action.optInt("target", -1).takeIf { it > 0 }
        if (target != null) {
            val element = currentElements.firstOrNull { it.index == target }
            if (element != null) {
                return clickElement(element, longPress = longPress)
            }
        }

        // The planner may name the element in words ("the text 'Wi-Fi' ...").
        val description = action.optString("element_text").trim()
        if (description.isNotEmpty()) {
            val element = resolveByDescription(description)
            if (element != null) {
                return clickElement(element, longPress = longPress)
            }
        }

        // Coordinate fallback: used when neither target nor element_text matched
        // an accessible element (e.g. an unlabelled icon or custom canvas view).
        if (action.has("x") && action.has("y")) {
            val (width, height) = screenDimensions()
            val px = (action.optInt("x") / 1000f) * width
            val py = (action.optInt("y") / 1000f) * height
            val ok = tapAtPixels(px, py, durationMs = if (longPress) LONG_PRESS_DURATION_MS else TAP_DURATION_MS)
            val verb = if (longPress) "Long-pressed" else "Tapped"
            return if (ok) {
                ExecutionResult(true, "$verb coordinates (${action.optInt("x")}, ${action.optInt("y")})")
            } else {
                ExecutionResult(false, "Could not tap the coordinates")
            }
        }

        if (target != null) {
            return ExecutionResult(
                false,
                "Element $target is no longer on screen. Take a new look before acting.",
            )
        }
        if (description.isNotEmpty()) {
            return ExecutionResult(
                false,
                "No element matching '$description' is on screen. " +
                    "Take a new look and pick a listed element or supply x/y.",
            )
        }
        return ExecutionResult(false, "No target given: use an element index, a description or x/y")
    }

    /**
     * Clicks the element itself (dynamic-first), walking up to clickable
     * ancestors or checkable siblings, and falling back to its recorded centre
     * when the node hierarchy does not accept an accessibility click.
     */
    private suspend fun clickElement(element: ScreenElement, longPress: Boolean = false): ExecutionResult {
        val actionId = if (longPress) {
            AccessibilityNodeInfo.ACTION_LONG_CLICK
        } else {
            AccessibilityNodeInfo.ACTION_CLICK
        }
        val verb = if (longPress) "Long-pressed" else "Clicked"

        if (!longPress && element.node.isEditable) {
            element.node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            if (element.node.performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                return ExecutionResult(true, "Focused '${element.label}'")
            }
        }

        // Walk up the ancestor chain (up to 5 levels) to find the clickable row/container.
        var cursor: AccessibilityNodeInfo? = element.node
        var depth = 0
        while (cursor != null && depth < 5) {
            val canAct = if (longPress) cursor.isLongClickable else (cursor.isClickable || cursor.isCheckable)
            if (canAct && cursor.performAction(actionId)) {
                return ExecutionResult(true, "$verb '${element.label}'")
            }
            cursor = cursor.parent
            depth++
        }

        // If a settings row label is not clickable itself, check if a sibling Switch/CheckBox is.
        if (!longPress) {
            val parent = element.node.parent
            if (parent != null) {
                for (i in 0 until parent.childCount) {
                    val sibling = parent.getChild(i) ?: continue
                    if ((sibling.isCheckable || sibling.isClickable) &&
                        sibling.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                    ) {
                        return ExecutionResult(true, "Toggled '${element.label}'")
                    }
                }
            }
        }

        val durationMs = if (longPress) LONG_PRESS_DURATION_MS else TAP_DURATION_MS
        val ok = tapAtPixels(element.centerX.toFloat(), element.centerY.toFloat(), durationMs = durationMs)
        return if (ok) {
            ExecutionResult(true, "Tapped '${element.label}'")
        } else {
            ExecutionResult(false, "Could not tap '${element.label}'")
        }
    }

    /**
     * Resolves a natural-language element description against the current
     * screen, the way ARTEMIS' screen index answers a text query: exact label
     * first, then containment, then token overlap.
     */
    private fun resolveByDescription(description: String): ScreenElement? {
        val query = normalize(quotedPhrase(description) ?: stripBoilerplate(description))
        if (query.isEmpty()) return null
        if (currentElements.isEmpty()) return null

        currentElements.firstOrNull { normalize(it.label) == query }?.let { return it }
        currentElements.firstOrNull {
            normalize(it.resourceId.substringAfterLast('/')) == query
        }?.let { return it }

        val contained = currentElements.filter { element ->
            val label = normalize(element.label)
            label.isNotEmpty() && (label.contains(query) || query.contains(label))
        }
        contained.minByOrNull { it.label.length }?.let { return it }

        val queryTokens = query.split(' ').filter { it.isNotEmpty() }.toSet()
        if (queryTokens.isEmpty()) return null
        return currentElements
            .mapNotNull { element ->
                val labelTokens = normalize(element.label).split(' ').filter { it.isNotEmpty() }.toSet()
                if (labelTokens.isEmpty()) {
                    null
                } else {
                    val overlap = queryTokens.intersect(labelTokens).size.toFloat() /
                        queryTokens.union(labelTokens).size
                    element to overlap
                }
            }
            .filter { it.second >= 0.45f }
            .maxByOrNull { it.second }
            ?.first
    }

    /** Picks the quoted phrase out of a description, if there is one. */
    private fun quotedPhrase(description: String): String? {
        val single = Regex("'([^']+)'").find(description)
        if (single != null) return single.groupValues[1]
        val double = Regex("\"([^\"]+)\"").find(description)
        if (double != null) return double.groupValues[1]
        return null
    }

    private fun stripBoilerplate(description: String): String =
        description
            .replace(Regex("^(?:the\\s+)?(?:text|field|button|input|switch|toggle|item|element|icon)\\s+", RegexOption.IGNORE_CASE), "")
            .replace(Regex("\\s+in\\s+the\\s+list$", RegexOption.IGNORE_CASE), "")
            .trim()

    private fun normalize(value: String): String =
        value.lowercase()
            .replace(Regex("[^\\p{L}\\p{N} ]"), " ")
            .replace(Regex("\\s+"), " ")
            .trim()

    private suspend fun tapAtPixels(
        x: Float,
        y: Float,
        durationMs: Long = TAP_DURATION_MS,
    ): Boolean {
        val (width, height) = screenDimensions()
        val safeX = x.coerceIn(1f, (width - 1).coerceAtLeast(1).toFloat())
        val safeY = y.coerceIn(1f, (height - 1).coerceAtLeast(1).toFloat())
        val path = Path().apply { moveTo(safeX, safeY) }
        val gesture = Builder()
            .addStroke(StrokeDescription(path, 0, durationMs))
            .build()
        return dispatchGestureAndWait(gesture)
    }

    private suspend fun dispatchGestureAndWait(gesture: GestureDescription): Boolean =
        suspendCancellableCoroutine { continuation ->
            val dispatched = service.dispatchGesture(
                gesture,
                object : AccessibilityService.GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        if (continuation.isActive) continuation.resume(true)
                    }

                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        if (continuation.isActive) continuation.resume(false)
                    }
                },
                null,
            )
            if (!dispatched && continuation.isActive) {
                continuation.resume(false)
            }
        }

    // --------------------------------------------------------------- typing

    private suspend fun typeText(action: JSONObject): ExecutionResult {
        val text = action.optString("text")
        if (text.isEmpty()) return ExecutionResult(false, "No text to type")

        val target = action.optInt("target", -1).takeIf { it > 0 }
        val description = action.optString("element_text").trim()

        var targetElement: ScreenElement? = null
        if (target != null) {
            targetElement = currentElements.firstOrNull { it.index == target }
        }
        if (targetElement == null && description.isNotEmpty()) {
            targetElement = resolveByDescription(description)
        }

        var node: AccessibilityNodeInfo? = targetElement?.node

        // If the chosen element is a non-editable search bar wrapper, click/tap it
        // first so the app opens the real EditText.
        if (node != null && !node.isEditable) {
            val childEditable = findEditableInSubtree(node)
            if (childEditable != null) {
                node = childEditable
            } else if (targetElement != null) {
                clickElement(targetElement, longPress = false)
                delay(350)
                node = service.rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                    ?: findFirstEditable()
            }
        }

        if (node == null && action.has("x") && action.has("y")) {
            val (width, height) = screenDimensions()
            tapAtPixels(
                (action.optInt("x") / 1000f) * width,
                (action.optInt("y") / 1000f) * height,
            )
            delay(350)
        }

        if (node == null) {
            node = service.rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                ?: findFirstEditable()
        }

        if (node == null) {
            return ExecutionResult(
                false,
                "No editable text field is open on screen. Tap the search/input bar first.",
            )
        }

        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        val arguments = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                text,
            )
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
        if (!ok) {
            return ExecutionResult(false, "The text field refused the input")
        }

        val shouldSubmit = action.optBoolean("submit", false)
        if (shouldSubmit && Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            delay(120)
            node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
            return ExecutionResult(true, "Typed '$text' and submitted")
        }
        return ExecutionResult(true, "Typed '$text'")
    }

    private fun pressEnter(): ExecutionResult {
        val node = service.rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?: findFirstEditable()
            ?: return ExecutionResult(false, "No focused input field to submit")
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)) {
                return ExecutionResult(true, "Pressed Enter")
            }
        }
        return ExecutionResult(false, "Could not trigger Enter on the current field")
    }

    // -------------------------------------------------------------- scrolling

    /**
     * Scrolls the content.
     *
     * Model semantics: "down" reveals the content *below* the current
     * viewport. The gesture is a slow drag (not a fling) that keeps a large
     * overlap with the previous viewport, so no row is skipped and the screen
     * never keeps moving after the action.
     */
    private suspend fun scroll(direction: String, distance: Float, target: Int?): ExecutionResult {
        val (wInt, hInt) = screenDimensions()
        val width = wInt.toFloat()
        val height = hInt.toFloat()
        val reach = (0.48f * distance.coerceIn(0.25f, 1f))

        val path = Path()
        when (direction) {
            // Reveal the content below: drag the finger upwards.
            "down" -> {
                val startY = height * 0.78f
                path.moveTo(width / 2f, startY)
                path.lineTo(width / 2f, startY - height * reach)
            }
            // Reveal the content above: drag the finger downwards.
            "up" -> {
                val startY = height * 0.30f
                path.moveTo(width / 2f, startY)
                path.lineTo(width / 2f, startY + height * reach)
            }
            "left" -> {
                val startX = width * 0.80f
                path.moveTo(startX, height / 2f)
                path.lineTo(startX - width * 0.48f * distance.coerceIn(0.25f, 1f), height / 2f)
            }
            else -> {
                val startX = width * 0.20f
                path.moveTo(startX, height / 2f)
                path.lineTo(startX + width * 0.48f * distance.coerceIn(0.25f, 1f), height / 2f)
            }
        }

        val gesture = Builder().addStroke(StrokeDescription(path, 0, SCROLL_DURATION_MS)).build()
        if (dispatchGestureAndWait(gesture)) {
            return ExecutionResult(true, "Scrolled $direction")
        }

        // Fallback: ask a scrollable container to scroll itself.
        val node = if (target != null) {
            currentElements.firstOrNull { it.index == target }?.node
        } else {
            findScrollable()
        }
        if (node != null) {
            val action = if (direction == "down" || direction == "right") {
                AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            } else {
                AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            }
            if (node.performAction(action)) {
                return ExecutionResult(true, "Scrolled $direction")
            }
        }
        return ExecutionResult(false, "Could not scroll $direction")
    }

    // --------------------------------------------------------------- helpers

    private fun global(action: Int, name: String): ExecutionResult =
        if (service.performGlobalAction(action)) {
            ExecutionResult(true, "Pressed $name")
        } else {
            ExecutionResult(false, "Could not press $name")
        }

    private fun openApp(packageName: String): ExecutionResult {
        if (packageName.isBlank()) return ExecutionResult(false, "No app given")
        val resolved = resolvePackage(packageName)
            ?: return ExecutionResult(false, "'$packageName' is not installed")
        val intent = service.packageManager.getLaunchIntentForPackage(resolved)
            ?: return ExecutionResult(false, "'$packageName' cannot be launched")
        intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
        service.startActivity(intent)
        return ExecutionResult(true, "Opened $packageName")
    }

    /**
     * Accepts either a package name or an app label ("Settings"), so the
     * planner can name an app the way a person would.
     */
    private fun resolvePackage(name: String): String? {
        val cleaned = name.trim()
        if (cleaned.contains('.')) {
            if (service.packageManager.getLaunchIntentForPackage(cleaned) != null) return cleaned
        }
        val alias = COMMON_PACKAGE_ALIASES[cleaned.lowercase()]
        if (alias != null && service.packageManager.getLaunchIntentForPackage(alias) != null) {
            return alias
        }
        val manager = service.packageManager
        val installed = runCatching { manager.getInstalledApplications(0) }.getOrNull() ?: return null
        val exact = installed.firstOrNull { app ->
            runCatching { manager.getApplicationLabel(app).toString() }
                .getOrNull()
                ?.equals(cleaned, ignoreCase = true) == true
        }
        if (exact != null) return exact.packageName
        val partial = installed.firstOrNull { app ->
            runCatching { manager.getApplicationLabel(app).toString() }
                .getOrNull()
                ?.contains(cleaned, ignoreCase = true) == true
        }
        return partial?.packageName
    }

    private fun findScrollable(): AccessibilityNodeInfo? {
        val (_, roots) = collectActiveRoots()
        val queue = ArrayDeque<AccessibilityNodeInfo>(roots)
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

    private fun findEditableInSubtree(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0
        while (queue.isNotEmpty() && visited < 64) {
            val node = queue.removeFirst()
            visited++
            if (node.isEditable) return node
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }
        return null
    }

    private fun findFirstEditable(): AccessibilityNodeInfo? {
        val (_, roots) = collectActiveRoots()
        val queue = ArrayDeque<AccessibilityNodeInfo>(roots)
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
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
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
                            val copy = wrapped?.copy(Bitmap.Config.ARGB_8888, false)
                            wrapped?.recycle()
                            hardware.close()
                            copy
                        } catch (_: Exception) {
                            null
                        }
                        if (continuation.isActive) continuation.resume(bitmap)
                    }

                    override fun onFailure(errorCode: Int) {
                        if (continuation.isActive) continuation.resume(null)
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

        const val TAP_DURATION_MS = 80L
        const val LONG_PRESS_DURATION_MS = 650L
        /** A slow drag instead of a fling, so the viewport settles predictably. */
        const val SCROLL_DURATION_MS = 700L

        val COMMON_PACKAGE_ALIASES = mapOf(
            "settings" to "com.android.settings",
            "system settings" to "com.android.settings",
            "chrome" to "com.android.chrome",
            "browser" to "com.android.chrome",
            "youtube" to "com.google.android.youtube",
            "whatsapp" to "com.whatsapp",
            "telegram" to "org.telegram.messenger",
            "gmail" to "com.google.android.gm",
            "maps" to "com.google.android.apps.maps",
            "google maps" to "com.google.android.apps.maps",
            "play store" to "com.android.vending",
            "google play" to "com.android.vending",
            "photos" to "com.google.android.apps.photos",
            "camera" to "com.android.camera",
            "calculator" to "com.google.android.calculator",
            "clock" to "com.google.android.deskclock",
            "calendar" to "com.google.android.calendar",
            "contacts" to "com.google.android.contacts",
            "messages" to "com.google.android.apps.messaging",
            "phone" to "com.google.android.dialer",
            "dialer" to "com.google.android.dialer",
            "files" to "com.google.android.documentsui",
            "spotify" to "com.spotify.music",
        )
    }
}
