package ai.oyi.device.ui

import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandAction
import ai.oyi.device.ui.island.IslandIdleInfo
import ai.oyi.device.ui.theme.GlassBackdrop
import ai.oyi.device.ui.theme.LocalGlassBackdrop
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.PressMorph
import ai.oyi.device.ui.theme.liquidGlass
import kotlin.math.roundToInt

@Composable
fun MainScreen(viewModel: MainViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val level by viewModel.voiceLevel.collectAsStateWithLifecycle()
    var tab by remember { mutableIntStateOf(0) }
    val context = LocalContext.current

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) viewModel.refresh()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    val space = remember(viewModel) {
        SpaceActions(
            openOverlay = {
                context.startActivity(
                    Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:${context.packageName}")),
                )
            },
            openUsage = { context.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)) },
            openAccessibility = { context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) },
            setOverlayEnabled = viewModel::setOverlayEnabled,
            setAutoStart = viewModel::setAutoStart,
            previewIsland = viewModel::previewIslandState,
            previewStack = viewModel::previewStack,
            setWallpaperColors = viewModel::setWallpaperColors,
            setLiveUpdates = viewModel::setLiveUpdates,
            setIslandNudge = viewModel::setIslandNudge,
            saveGateway = viewModel::saveGateway,
        )
    }

    MainShell(
        state = state,
        level = level,
        tab = tab,
        onTab = { tab = it },
        onIslandAction = { action ->
            when (action) {
                IslandAction.APPROVE -> viewModel.approveIsland()
                IslandAction.DENY -> viewModel.denyIsland()
                IslandAction.STOP -> viewModel.stopActiveTask()
                IslandAction.DISMISS -> viewModel.dismissIsland()
                IslandAction.INTERRUPT -> viewModel.interruptAgent()
                IslandAction.SWAP -> viewModel.swapIsland()
                IslandAction.OPEN_APP -> Unit
            }
        },
    ) { current ->
        when (current) {
            0 -> FocusScreen()
            1 -> SpaceContent(state, space)
            else -> ChatScreen()
        }
    }
}

/** Everything the Space tab can ask for. The defaults do nothing, for previews. */
class SpaceActions(
    val openOverlay: () -> Unit = {},
    val openUsage: () -> Unit = {},
    val openAccessibility: () -> Unit = {},
    val setOverlayEnabled: (Boolean) -> Unit = {},
    val setAutoStart: (Boolean) -> Unit = {},
    val previewIsland: (IslandKind) -> Unit = {},
    val previewStack: () -> Unit = {},
    val setWallpaperColors: (Boolean) -> Unit = {},
    val setLiveUpdates: (Boolean) -> Unit = {},
    val setIslandNudge: (Int, Int) -> Unit = { _, _ -> },
    val saveGateway: (String, String) -> Unit = { _, _ -> },
)

/** The app's frame: backdrop, the island, the tabs and the current tab. */
@Composable
fun MainShell(
    state: UiState,
    level: Float,
    tab: Int,
    onTab: (Int) -> Unit,
    onIslandAction: (IslandAction) -> Unit,
    content: @Composable (tab: Int) -> Unit,
) {
    GlassBackdrop {
        Scaffold(containerColor = Color.Transparent) { padding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 6.dp, bottom = 4.dp),
                    contentAlignment = Alignment.TopCenter,
                ) {
                    DynamicIsland(
                        state = state.island,
                        idle = IslandIdleInfo(
                            appLabel = "",
                            link = state.connection,
                            linkError = state.connectionError,
                        ),
                        level = level,
                        inApp = true,
                        secondary = state.islandSecondary,
                        accent = MaterialTheme.colorScheme.primary,
                        onAction = onIslandAction,
                        modifier = Modifier.padding(horizontal = 16.dp),
                    )
                }

                ExpressiveTabs(
                    tabs = listOf("Focus", "Space", "Live Chat"),
                    selected = tab,
                    onSelect = onTab,
                )

                content(tab)
            }
        }
    }
}

/**
 * A Material 3 Expressive button group on a glass track: the selected tab
 * is a solid pill that slides between the others on a spatial spring.
 */
@Composable
private fun ExpressiveTabs(
    tabs: List<String>,
    selected: Int,
    onSelect: (Int) -> Unit,
) {
    val trackShape = RoundedCornerShape(50)
    BoxWithConstraints(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .height(48.dp)
            .liquidGlass(trackShape, fill = Oyi.SmokedGlass, backdrop = LocalGlassBackdrop.current, elevation = 8.dp)
            .padding(4.dp),
    ) {
        val slot = maxWidth / tabs.size
        val indicatorX by animateDpAsState(
            targetValue = slot * selected,
            animationSpec = OyiMotion.defaultSpatial(),
            label = "tabIndicator",
        )
        Box(
            Modifier
                .offset(x = indicatorX)
                .width(slot)
                .fillMaxHeight()
                .clip(CircleShape)
                .background(Brush.verticalGradient(listOf(Color(0xFFFFF8E6), Oyi.Cream))),
        )
        Row(Modifier.fillMaxSize()) {
            tabs.forEachIndexed { index, title ->
                val isSelected = index == selected
                val color by animateColorAsState(
                    targetValue = if (isSelected) Oyi.Ink else Oyi.Muted,
                    animationSpec = OyiMotion.defaultEffects(),
                    label = "tabText",
                )
                PressMorph(
                    onClick = { onSelect(index) },
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight(),
                ) {
                    Text(
                        text = title,
                        color = color,
                        fontSize = 14.sp,
                        fontWeight = if (isSelected) FontWeight.ExtraBold else FontWeight.SemiBold,
                        modifier = Modifier.align(Alignment.Center),
                    )
                }
            }
        }
    }
}

/** The Space tab: access, screen time, the island and the gateway link. */
@Composable
fun SpaceContent(state: UiState, actions: SpaceActions) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Spacer(Modifier.height(6.dp))
            Text(
                text = "My\nDevice",
                style = MaterialTheme.typography.displaySmall,
                color = Oyi.OffWhite,
            )
            Spacer(Modifier.height(4.dp))
        }

        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Box(modifier = Modifier.weight(1f)) {
                    PermissionsBentoCard(
                        state = state,
                        onOpenOverlay = actions.openOverlay,
                        onOpenUsage = actions.openUsage,
                        onOpenAccessibility = actions.openAccessibility,
                    )
                }
                Box(modifier = Modifier.weight(1f)) {
                    UsageBentoCard(state = state)
                }
            }
        }

        item {
            RunCard(
                enabled = state.overlayEnabled,
                autoStart = state.autoStart,
                onToggle = actions.setOverlayEnabled,
                onAutoStart = actions.setAutoStart,
                onPreviewIsland = actions.previewIsland,
                onPreviewStack = actions.previewStack,
            )
        }

        item {
            IslandLookCard(
                state = state,
                onWallpaper = actions.setWallpaperColors,
                onLiveUpdates = actions.setLiveUpdates,
                onNudge = actions.setIslandNudge,
            )
        }

        item {
            GatewayCard(
                state = state,
                onSave = actions.saveGateway,
            )
        }

        item {
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable
private fun PermissionsBentoCard(
    state: UiState,
    onOpenOverlay: () -> Unit,
    onOpenUsage: () -> Unit,
    onOpenAccessibility: () -> Unit,
) {
    GlassCard(brush = Oyi.OrangeGlass, modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                text = "Device\nAccess",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.ExtraBold,
                color = Oyi.Ink,
            )
            Spacer(Modifier.height(2.dp))
            BentoChecklistItem("Island", state.overlayPermission, onOpenOverlay)
            BentoChecklistItem("Usage", state.usagePermission, onOpenUsage)
            BentoChecklistItem("Control", state.accessibilityEnabled, onOpenAccessibility)
        }
    }
}

@Composable
private fun BentoChecklistItem(label: String, granted: Boolean, onClick: () -> Unit) {
    PressMorph(
        onClick = { if (!granted) onClick() },
        modifier = Modifier.fillMaxWidth(),
        pressedPercent = 30f,
    ) { shape ->
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(Color(0x260A0807), shape)
                .padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(18.dp)
                    .clip(CircleShape)
                    .background(if (granted) Oyi.Ink else Color(0x260A0807)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (granted) "✓" else "○",
                    color = if (granted) Color(0xFFFFF8E7) else Oyi.Ink,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                text = label,
                color = Oyi.Ink,
                fontSize = 12.5.sp,
                fontWeight = FontWeight.ExtraBold,
            )
        }
    }
}

@Composable
private fun UsageBentoCard(state: UiState) {
    GlassCard(brush = Oyi.YellowGlass, modifier = Modifier.fillMaxWidth()) {
        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                text = "Screen\nTime",
                style = MaterialTheme.typography.titleLarge,
                fontWeight = FontWeight.ExtraBold,
                color = Oyi.Ink,
            )
            Text(
                text = state.currentApp?.let { "Now · ${it.label}" } ?: "Updated live",
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = Color(0xFF2E2810),
            )
            Spacer(Modifier.height(4.dp))
            if (state.topApps.isEmpty()) {
                Text(text = "No usage yet", fontSize = 12.sp, color = Color(0xFF2E2810))
            } else {
                state.topApps.take(3).forEach { app ->
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            text = app.label,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            color = Oyi.Ink,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            text = formatDuration(app.totalMs),
                            fontSize = 11.5.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            color = Oyi.Ink,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun creamSwitchColors() = SwitchDefaults.colors(
    checkedThumbColor = Oyi.Cream,
    checkedTrackColor = Oyi.Ink,
    uncheckedThumbColor = Oyi.Ink,
    uncheckedTrackColor = Color(0xFFDAD0B2),
)

@Composable
private fun RunCard(
    enabled: Boolean,
    autoStart: Boolean,
    onToggle: (Boolean) -> Unit,
    onAutoStart: (Boolean) -> Unit,
    onPreviewIsland: (IslandKind) -> Unit,
    onPreviewStack: () -> Unit,
) {
    GlassCard(brush = Oyi.CreamGlass) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = if (enabled) "Active across apps" else "Stopped",
                    fontSize = 11.5.sp,
                    fontWeight = FontWeight.Medium,
                    color = Oyi.InkSoft,
                )
                Text(
                    text = "Dynamic Island",
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.ExtraBold,
                    color = Oyi.Ink,
                )
            }
            Switch(checked = enabled, onCheckedChange = onToggle, colors = creamSwitchColors())
        }
        SwitchRow("Launch on boot", autoStart, onAutoStart)
        Spacer(Modifier.height(10.dp))
        Text(
            text = "Preview the island",
            fontSize = 11.5.sp,
            fontWeight = FontWeight.Medium,
            color = Oyi.InkSoft,
            modifier = Modifier.padding(bottom = 6.dp),
        )
        val previewRows = listOf(
            listOf(
                "Listen" to IslandKind.LISTENING,
                "Think" to IslandKind.THINKING,
                "Tool" to IslandKind.TOOL,
                "Speak" to IslandKind.SPEAKING,
            ),
            listOf(
                "Idle" to IslandKind.IDLE,
                "Phone" to IslandKind.WORKING,
                "Approve" to IslandKind.APPROVAL_REQUIRED,
                "Done" to IslandKind.COMPLETED,
            ),
        )
        previewRows.forEachIndexed { rowIndex, modes ->
            if (rowIndex > 0) Spacer(Modifier.height(6.dp))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                modes.forEach { (label, kind) ->
                    Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                        Pill(text = label, onDark = false) { onPreviewIsland(kind) }
                    }
                }
            }
        }
        Spacer(Modifier.height(6.dp))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Pill(text = "Two at once", selected = true, onDark = false, onClick = onPreviewStack)
        }
    }
}

@Composable
private fun SwitchRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit, detail: String = "") {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(text = label, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, color = Oyi.Ink)
            if (detail.isNotBlank()) {
                Text(text = detail, fontSize = 11.5.sp, color = Oyi.InkSoft, lineHeight = 15.sp)
            }
        }
        Spacer(Modifier.width(8.dp))
        Switch(checked = checked, onCheckedChange = onChange, colors = creamSwitchColors())
    }
}

/** How the island looks and where it sits over the camera. */
@Composable
private fun IslandLookCard(
    state: UiState,
    onWallpaper: (Boolean) -> Unit,
    onLiveUpdates: (Boolean) -> Unit,
    onNudge: (Int, Int) -> Unit,
) {
    GlassCard(brush = Oyi.CreamGlass) {
        Text(
            text = "Island look",
            style = MaterialTheme.typography.titleLarge,
            fontWeight = FontWeight.ExtraBold,
            color = Oyi.Ink,
        )
        SwitchRow(
            label = "Match wallpaper colours",
            checked = state.wallpaperColors,
            onChange = onWallpaper,
            detail = if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S) {
                "Material You accent instead of OYI orange."
            } else {
                "Needs Android 12 or newer."
            },
        )
        SwitchRow(
            label = "Live Update when hidden",
            checked = state.liveUpdates,
            onChange = onLiveUpdates,
            detail = "Shows what OYI is doing as a status bar chip (Android 16) when the island is off.",
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text = "Fit over the camera",
            fontSize = 13.5.sp,
            fontWeight = FontWeight.SemiBold,
            color = Oyi.Ink,
        )
        Text(
            text = "The island centres itself on the camera hole. Nudge it if it is a little off.",
            fontSize = 11.5.sp,
            color = Oyi.InkSoft,
            lineHeight = 15.sp,
        )
        var x by remember(state.islandNudgeXDp) { mutableFloatStateOf(state.islandNudgeXDp.toFloat()) }
        var y by remember(state.islandNudgeYDp) { mutableFloatStateOf(state.islandNudgeYDp.toFloat()) }
        NudgeSlider("Left · Right", x, onChange = { x = it }) { onNudge(x.roundToInt(), y.roundToInt()) }
        NudgeSlider("Up · Down", y, onChange = { y = it }) { onNudge(x.roundToInt(), y.roundToInt()) }
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            Pill(text = "Reset", onDark = false, enabled = x != 0f || y != 0f) {
                x = 0f
                y = 0f
                onNudge(0, 0)
            }
        }
    }
}

@Composable
private fun NudgeSlider(label: String, value: Float, onChange: (Float) -> Unit, onDone: () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
        Text(
            text = label,
            fontSize = 11.5.sp,
            fontWeight = FontWeight.Bold,
            color = Oyi.InkSoft,
            modifier = Modifier.width(84.dp),
        )
        Slider(
            value = value,
            onValueChange = onChange,
            onValueChangeFinished = onDone,
            valueRange = -24f..24f,
            steps = 47,
            colors = SliderDefaults.colors(
                thumbColor = Oyi.Ink,
                activeTrackColor = Oyi.Ink,
                inactiveTrackColor = Color(0x330A0807),
                activeTickColor = Color.Transparent,
                inactiveTickColor = Color.Transparent,
            ),
            modifier = Modifier.weight(1f),
        )
        Text(
            text = "${value.roundToInt()}",
            fontSize = 11.5.sp,
            fontFamily = FontFamily.Monospace,
            color = Oyi.Ink,
            modifier = Modifier.width(28.dp),
        )
    }
}

@Composable
private fun GatewayCard(
    state: UiState,
    onSave: (String, String) -> Unit,
) {
    var url by remember(state.gatewayUrl) { mutableStateOf(state.gatewayUrl) }
    var token by remember(state.token) { mutableStateOf(state.token) }
    val fieldColors = OutlinedTextFieldDefaults.colors(
        focusedBorderColor = Oyi.Cream,
        unfocusedBorderColor = Color(0x33FFFFFF),
        focusedLabelColor = Oyi.Cream,
        cursorColor = Oyi.Cream,
        focusedContainerColor = Color(0x14FFFFFF),
        unfocusedContainerColor = Color(0x0AFFFFFF),
    )

    GlassCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Gateway Link",
                style = MaterialTheme.typography.titleLarge,
                color = Oyi.OffWhite,
                modifier = Modifier.weight(1f),
            )
            ConnectionBadge(state.connection, state.connectionError)
        }
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = url,
            onValueChange = { url = it },
            label = { Text("Gateway URL") },
            placeholder = { Text("https://oyi.example") },
            singleLine = true,
            shape = RoundedCornerShape(20.dp),
            colors = fieldColors,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = token,
            onValueChange = { token = it },
            label = { Text("Pairing token") },
            singleLine = true,
            shape = RoundedCornerShape(20.dp),
            colors = fieldColors,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Pill(text = "Save link", selected = true) { onSave(url, token) }
            Spacer(Modifier.weight(1f))
            if (state.deviceId.isNotBlank()) {
                Text(
                    text = state.deviceId.take(8),
                    style = MaterialTheme.typography.labelSmall,
                    fontFamily = FontFamily.Monospace,
                    color = Oyi.Muted,
                )
            }
        }
    }
}

@Composable
private fun ConnectionBadge(connection: ConnectionState, error: String) {
    val (label, color) = when (connection) {
        ConnectionState.CONNECTED -> "Connected" to Oyi.Cream
        ConnectionState.CONNECTING -> "Connecting" to Oyi.Yellow
        ConnectionState.ERROR -> "Error" to Oyi.Orange
        ConnectionState.DISCONNECTED -> "Offline" to Oyi.Muted
    }
    Column(horizontalAlignment = Alignment.End) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(6.dp))
            Text(label, color = color, style = MaterialTheme.typography.labelLarge)
        }
        if (error.isNotBlank()) {
            Text(
                text = error.take(40),
                style = MaterialTheme.typography.labelSmall,
                color = Oyi.Muted,
            )
        }
    }
}

private fun formatDuration(millis: Long): String {
    val minutes = millis / 60_000
    val hours = minutes / 60
    val rest = minutes % 60
    return when {
        hours > 0 -> "${hours}h ${rest}m"
        minutes > 0 -> "${minutes}m"
        else -> "<1m"
    }
}
