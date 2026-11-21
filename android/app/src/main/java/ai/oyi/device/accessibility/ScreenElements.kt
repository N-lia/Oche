package ai.oyi.device.accessibility

import android.graphics.Rect
import android.os.Build
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

/**
 * The visible element index of one screen.
 *
 * This mirrors the observation format used by Google's ARTEMIS phone-use
 * framework: the model is shown a short, numbered list of the *labelled*
 * elements that are actually on screen, and it refers to them by index. The
 * device resolves the index against the live node it captured, so a tap lands
 * on the element the model meant even if the layout moved in between.
 *
 * Dynamic-First, Coordinate-Fallback (ARTEMIS' rule): an index is always
 * preferred; raw coordinates are only a fallback for custom-drawn UI that has
 * no accessible element at all.
 */
data class ScreenElement(
    /** 1-based index, as shown to the model. */
    val index: Int,
    val label: String,
    /** "Text", "Input", "Button", "Switch", or "Hint". */
    val kind: String,
    /** Pixel bounds of the element. */
    val bounds: Rect,
    val className: String,
    val resourceId: String,
    val clickable: Boolean = false,
    val editable: Boolean = false,
    val checkable: Boolean = false,
    val checked: Boolean = false,
    val focused: Boolean = false,
    val scrollable: Boolean = false,
    /** The live node, used to execute the action against the real element. */
    val node: AccessibilityNodeInfo,
) {
    val centerX: Int get() = bounds.centerX()
    val centerY: Int get() = bounds.centerY()
}

object ScreenElements {

    const val MAX_ELEMENTS = 140

    /** Same-label elements within this distance (px) are the same element. */
    private const val DUPLICATE_RADIUS = 10f

    /**
     * Walks the live tree and returns the visible, actionable or labelled
     * elements in traversal order.
     */
    fun collect(
        root: AccessibilityNodeInfo?,
        width: Int,
        height: Int,
    ): List<ScreenElement> {
        if (root == null) return emptyList()
        return collectRoots(listOf(root), width, height)
    }

    fun collectRoots(
        roots: List<AccessibilityNodeInfo>,
        width: Int,
        height: Int,
    ): List<ScreenElement> {
        if (roots.isEmpty()) return emptyList()

        val elements = mutableListOf<ScreenElement>()
        val seen = mutableListOf<Pair<String, Pair<Int, Int>>>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        roots.forEach { queue.add(it) }

        while (queue.isNotEmpty() && elements.size < MAX_ELEMENTS) {
            val node = queue.removeFirst()
            if (!node.isVisibleToUser) {
                continue
            }
            val label = labelOf(node)
            if (label.isNotEmpty()) {
                val bounds = Rect()
                node.getBoundsInScreen(bounds)
                if (isVisible(bounds, width, height)) {
                    val kind = kindOf(node)
                    val dedupKey = "$kind:$label"
                    val center = bounds.centerX() to bounds.centerY()
                    val duplicate = seen.any { (key, point) ->
                        key == dedupKey &&
                            abs(point.first - center.first) < DUPLICATE_RADIUS &&
                            abs(point.second - center.second) < DUPLICATE_RADIUS
                    }
                    if (!duplicate) {
                        seen.add(dedupKey to center)
                        val checkState = resolveCheckState(node)
                        elements.add(
                            ScreenElement(
                                index = elements.size + 1,
                                label = label,
                                kind = kind,
                                bounds = Rect(bounds),
                                className = node.className?.toString().orEmpty(),
                                resourceId = node.viewIdResourceName.orEmpty(),
                                clickable = isEffectivelyClickable(node),
                                editable = node.isEditable,
                                checkable = checkState.first,
                                checked = checkState.second,
                                focused = node.isFocused || node.isAccessibilityFocused,
                                scrollable = node.isScrollable,
                                node = node,
                            )
                        )
                    }
                }
            }
            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }
        return elements
    }

    /** JSON form sent to the server (no node references, only metadata). */
    fun toJson(elements: List<ScreenElement>): JSONArray {
        val array = JSONArray()
        elements.forEach { element ->
            array.put(
                JSONObject().apply {
                    put("index", element.index)
                    put("text", element.label)
                    put("kind", element.kind)
                    put("class", element.className)
                    put("id", element.resourceId)
                    put("clickable", element.clickable)
                    put("editable", element.editable)
                    put("checkable", element.checkable)
                    put("checked", element.checked)
                    put("focused", element.focused)
                    put("scrollable", element.scrollable)
                    put("bounds", JSONArray().apply {
                        put(element.bounds.left)
                        put(element.bounds.top)
                        put(element.bounds.right)
                        put(element.bounds.bottom)
                    })
                }
            )
        }
        return array
    }

    /**
     * The "Visible UI Elements" block, formatted like ARTEMIS: one line per
     * element with 0..1000 normalized bounds and interactive state tags.
     */
    fun format(elements: List<ScreenElement>, width: Int, height: Int): String {
        if (elements.isEmpty()) return "(no accessible elements found)"
        return elements.joinToString("\n") { element ->
            val bounds = element.bounds
            val normalized = normalize(bounds, width, height)
            buildString {
                append("[")
                append(element.index)
                append("] ")
                append(element.kind)
                append(": '")
                append(element.label.replace('\n', ' '))
                append("'")
                if (element.checkable) {
                    append(if (element.checked) " [ON]" else " [OFF]")
                } else if (element.checked) {
                    append(" [ON]")
                }
                if (element.editable) {
                    append(" [editable]")
                } else if (element.clickable) {
                    append(" [clickable]")
                }
                if (element.focused) {
                    append(" [focused]")
                }
                append(" | Bounds: ")
                append(normalized)
                if (element.resourceId.isNotEmpty()) {
                    append(" | id: ")
                    append(element.resourceId.substringAfterLast('/'))
                }
            }
        }
    }

    private fun normalize(bounds: Rect, width: Int, height: Int): String {
        val w = if (width > 0) width else bounds.width()
        val h = if (height > 0) height else bounds.height()
        val left = ((bounds.left * 1000f) / w).toInt().coerceIn(0, 1000)
        val top = ((bounds.top * 1000f) / h).toInt().coerceIn(0, 1000)
        val right = ((bounds.right * 1000f) / w).toInt().coerceIn(0, 1000)
        val bottom = ((bounds.bottom * 1000f) / h).toInt().coerceIn(0, 1000)
        return "[$left,$top][$right,$bottom]"
    }

    private fun labelOf(node: AccessibilityNodeInfo): String {
        node.text?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        node.contentDescription?.toString()?.trim()?.takeIf { it.isNotEmpty() }
            ?.let { return it }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            node.hintText?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            node.tooltipText?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            node.stateDescription?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        }
        // Editable inputs, checkable switches, or unlabelled icon buttons with a view ID.
        if (node.isEditable) {
            val shortId = node.viewIdResourceName?.substringAfterLast('/')?.replace('_', ' ')?.trim()
            return if (!shortId.isNullOrEmpty()) shortId else "Text input"
        }
        if (node.isCheckable) {
            siblingOrParentText(node)?.let { return it }
            val shortId = node.viewIdResourceName?.substringAfterLast('/')?.replace('_', ' ')?.trim()
            return if (!shortId.isNullOrEmpty()) shortId else "Toggle"
        }
        if (node.isClickable && node.childCount == 0) {
            val shortId = node.viewIdResourceName?.substringAfterLast('/')?.trim()
            if (!shortId.isNullOrEmpty()) {
                return shortId.replace('_', ' ')
            }
        }
        return ""
    }

    private fun siblingOrParentText(node: AccessibilityNodeInfo): String? {
        val parent = node.parent ?: return null
        for (i in 0 until parent.childCount) {
            val sibling = parent.getChild(i) ?: continue
            if (sibling == node) continue
            val text = sibling.text?.toString()?.trim()
                ?: sibling.contentDescription?.toString()?.trim()
            if (!text.isNullOrEmpty()) return text
        }
        return null
    }

    private fun resolveCheckState(node: AccessibilityNodeInfo): Pair<Boolean, Boolean> {
        if (node.isCheckable) return true to node.isChecked
        val parent = node.parent ?: return false to node.isChecked
        for (i in 0 until parent.childCount) {
            val sibling = parent.getChild(i) ?: continue
            if (sibling.isCheckable) {
                return true to sibling.isChecked
            }
        }
        return false to node.isChecked
    }

    private fun isEffectivelyClickable(node: AccessibilityNodeInfo): Boolean {
        var cursor: AccessibilityNodeInfo? = node
        var depth = 0
        while (cursor != null && depth < 4) {
            if (cursor.isClickable || cursor.isCheckable || cursor.isEditable) return true
            cursor = cursor.parent
            depth++
        }
        return false
    }

    private fun kindOf(node: AccessibilityNodeInfo): String {
        val cls = node.className?.toString().orEmpty()
        if (node.isEditable || cls.contains("EditText", ignoreCase = true)) return "Input"
        if (
            node.isCheckable ||
            cls.contains("Switch", ignoreCase = true) ||
            cls.contains("CheckBox", ignoreCase = true) ||
            cls.contains("ToggleButton", ignoreCase = true) ||
            cls.contains("RadioButton", ignoreCase = true)
        ) {
            return "Switch"
        }
        if (cls.contains("Button", ignoreCase = true)) return "Button"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            node.text.isNullOrBlank() &&
            !node.hintText.isNullOrBlank()
        ) {
            return "Input"
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P &&
            node.text.isNullOrBlank() &&
            !node.tooltipText.isNullOrBlank()
        ) {
            return "Hint"
        }
        return "Text"
    }

    private fun isVisible(bounds: Rect, width: Int, height: Int): Boolean {
        if (bounds.width() <= 0 || bounds.height() <= 0) return false
        if (bounds.right <= 0 || bounds.bottom <= 0) return false
        if (width > 0 && bounds.left >= width) return false
        if (height > 0 && bounds.top >= height) return false
        return true
    }
}
