package ai.oyi.device.accessibility

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * Flattens the accessibility tree into a compact JSON list the vision model
 * can reason about.  Only nodes that carry useful information are kept so the
 * prompt stays small on a 720x1600 budget phone.
 */
object UiTreeSerializer {

    private const val MAX_NODES = 120
    private const val MAX_TEXT = 120

    fun serialize(root: AccessibilityNodeInfo?): JSONArray {
        val out = JSONArray()
        if (root == null) return out

        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        var visited = 0

        while (queue.isNotEmpty() && visited < MAX_NODES) {
            val node = queue.removeFirst()
            visited++
            val bounds = Rect()
            node.getBoundsInScreen(bounds)

            val text = node.text?.toString()?.take(MAX_TEXT).orEmpty()
            val description = node.contentDescription?.toString()?.take(MAX_TEXT).orEmpty()
            val interesting = text.isNotBlank() ||
                description.isNotBlank() ||
                node.isClickable ||
                node.isEditable ||
                node.isScrollable

            if (interesting) {
                out.put(
                    JSONObject().apply {
                        put("text", text)
                        put("desc", description)
                        put("id", node.viewIdResourceName.orEmpty())
                        put("class", node.className?.toString().orEmpty())
                        put("bounds", JSONArray().apply {
                            put(bounds.left)
                            put(bounds.top)
                            put(bounds.right)
                            put(bounds.bottom)
                        })
                        put("clickable", node.isClickable)
                        put("editable", node.isEditable)
                        put("scrollable", node.isScrollable)
                        put("focused", node.isFocused)
                        put("enabled", node.isEnabled)
                        put("package", node.packageName?.toString().orEmpty())
                    },
                )
            }

            for (index in 0 until node.childCount) {
                node.getChild(index)?.let { queue.add(it) }
            }
        }

        return out
    }
}
