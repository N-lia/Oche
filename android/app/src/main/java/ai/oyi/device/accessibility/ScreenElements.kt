package ai.oyi.device.accessibility

import android.graphics.Rect
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
    /** "Text", "Hint" or "Tooltip". */
    val kind: String,
    /** Pixel bounds of the element. */
    val bounds: Rect,
    val className: String,
    val resourceId: String,
    /** The live node, used to execute the action against the real element. */
    val node: AccessibilityNodeInfo,
) {
    val centerX: Int get() = bounds.centerX()
    val centerY: Int get() = bounds.centerY()
}

object ScreenElements {

    const val MAX_ELEMENTS = 120

    /** Same-label elements within this distance (px) are the same element. */
    private const val DUPLICATE_RADIUS = 8f

    /**
     * Walks the live tree and returns the visible, labelled elements in
     * traversal order.
     *
     * Unlabelled wrappers are skipped: they answer neither a text query nor a
     * hit test. Off-screen and zero-area nodes are skipped as well.
     */
    fun collect(
        root: AccessibilityNodeInfo?,
        width: Int,
        height: Int,
    ): List<ScreenElement> {
        if (root == null) return emptyList()

        val elements = mutableListOf<ScreenElement>()
        val seen = mutableListOf<Pair<String, Pair<Int, Int>>>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)

        while (queue.isNotEmpty() && elements.size < MAX_ELEMENTS) {
            val node = queue.removeFirst()
            val label = labelOf(node)
            if (label.isNotEmpty()) {
                val bounds = Rect()
                node.getBoundsInScreen(bounds)
                if (isVisible(bounds, width, height)) {
                    val center = bounds.centerX() to bounds.centerY()
                    val duplicate = seen.any { (text, point) ->
                        text == label &&
                            abs(point.first - center.first) < DUPLICATE_RADIUS &&
                            abs(point.second - center.second) < DUPLICATE_RADIUS
                    }
                    if (!duplicate) {
                        seen.add(label to center)
                        elements.add(
                            ScreenElement(
                                index = elements.size + 1,
                                label = label,
                                kind = kindOf(node),
                                bounds = Rect(bounds),
                                className = node.className?.toString().orEmpty(),
                                resourceId = node.viewIdResourceName.orEmpty(),
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
     * element with 0..1000 normalized bounds.
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
                append("' | Bounds: ")
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
        node.tooltipText?.toString()?.trim()?.takeIf { it.isNotEmpty() }?.let { return it }
        return ""
    }

    private fun kindOf(node: AccessibilityNodeInfo): String {
        if (node.text?.toString()?.isNotBlank() == true) return "Text"
        if (node.tooltipText?.toString()?.isNotBlank() == true) return "Hint"
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
