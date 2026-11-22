package ai.oyi.device.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AppUpdaterTest {

    @Test
    fun readsBuildNumbers() {
        assertEquals(123, buildNumberOf("build-123"))
        assertNull(buildNumberOf("v1.2"))
        assertNull(buildNumberOf("build-x"))
    }

    @Test
    fun picksTheApkFromALatestRelease() {
        val json = """
            {"tag_name": "build-42", "body": "Island morphs bounce",
             "assets": [
               {"name": "notes.txt", "browser_download_url": "https://x/notes.txt", "size": 10},
               {"name": "oyi-build-42.apk", "browser_download_url": "https://x/oyi.apk", "size": 9000}
             ]}
        """.trimIndent()
        val release = parseRelease(json)!!
        assertEquals(42, release.build)
        assertEquals("https://x/oyi.apk", release.apkUrl)
        assertEquals(9000L, release.sizeBytes)
        assertEquals("Island morphs bounce", release.notes)
    }

    @Test
    fun ignoresReleasesThatAreNotBuilds() {
        assertNull(parseRelease("""{"tag_name": "v1", "assets": []}"""))
        assertNull(parseRelease("""{"tag_name": "build-3", "assets": []}"""))
    }
}
