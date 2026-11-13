package ai.oyi.device.focus

/** Reads the site out of a browser's address bar and matches it to the block list. */
object BrowserUrls {

    /** Address-bar view ids of the browsers the blocker can read. */
    val URL_BAR_IDS: Map<String, List<String>> = mapOf(
        "com.android.chrome" to listOf("com.android.chrome:id/url_bar"),
        "com.chrome.beta" to listOf("com.chrome.beta:id/url_bar"),
        "com.brave.browser" to listOf("com.brave.browser:id/url_bar"),
        "com.microsoft.emmx" to listOf("com.microsoft.emmx:id/url_bar"),
        "com.vivaldi.browser" to listOf("com.vivaldi.browser:id/url_bar"),
        "com.kiwibrowser.browser" to listOf("com.kiwibrowser.browser:id/url_bar"),
        "org.mozilla.firefox" to listOf(
            "org.mozilla.firefox:id/mozac_browser_toolbar_url_view",
            "org.mozilla.firefox:id/url_bar_title",
        ),
        "org.mozilla.focus" to listOf("org.mozilla.focus:id/mozac_browser_toolbar_url_view"),
        "com.opera.browser" to listOf("com.opera.browser:id/url_field"),
        "com.opera.mini.native" to listOf("com.opera.mini.native:id/url_field"),
        "com.sec.android.app.sbrowser" to listOf(
            "com.sec.android.app.sbrowser:id/location_bar_edit_text",
        ),
        "com.duckduckgo.mobile.android" to listOf(
            "com.duckduckgo.mobile.android:id/omnibarTextInput",
        ),
        "com.transsion.phoenix" to listOf(
            "com.transsion.phoenix:id/url_bar",
            "com.transsion.phoenix:id/search_text",
        ),
    )

    /**
     * The bare host of whatever the address bar shows, or null when it is not
     * a web address (a search query, an empty bar).
     */
    fun hostOf(raw: String): String? {
        var text = raw.trim().lowercase()
        if (text.isEmpty() || text.any { it.isWhitespace() }) return null
        val scheme = text.indexOf("://")
        if (scheme >= 0) text = text.substring(scheme + 3)
        val end = text.indexOfFirst { it == '/' || it == '?' || it == '#' }
        if (end >= 0) text = text.substring(0, end)
        val at = text.lastIndexOf('@')
        if (at >= 0) text = text.substring(at + 1)
        val port = text.indexOf(':')
        if (port >= 0) text = text.substring(0, port)
        text = text.removePrefix("www.").removePrefix("m.").trimEnd('.')
        if (!text.contains('.')) return null
        if (text.any { !(it.isLetterOrDigit() || it == '.' || it == '-') }) return null
        return text
    }

    /** True when [host] is [site] or one of its subdomains. */
    fun matches(host: String, site: String): Boolean =
        host == site || host.endsWith(".$site")
}
