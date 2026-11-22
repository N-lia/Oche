package ai.oyi.device.snapshots

import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.platform.LocalInspectionMode

/**
 * Renders in inspection mode, so the island's entrance pops and loops sit
 * at rest. Paparazzi captures the first frame, which otherwise shows every
 * animation at its starting point.
 */
@Composable
fun Still(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalInspectionMode provides true, content = content)
}
