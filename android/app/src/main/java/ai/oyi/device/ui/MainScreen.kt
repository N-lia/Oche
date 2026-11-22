package ai.oyi.device.ui

import ai.oyi.device.R
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandAction
import ai.oyi.device.ui.island.IslandIdleInfo
import ai.oyi.device.ui.theme.GlassBackdrop
import ai.oyi.device.ui.theme.LocalGlassBackdrop
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.PressMorph
import ai.oyi.device.ui.theme.Tabular
import androidx.compose.material3.LocalTextStyle
import ai.oyi.device.ui.theme.glassSource
import ai.oyi.device.ui.theme.liquidGlass
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import androidx.annotation.DrawableRes
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.Crossfade
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandHorizontally
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkHorizontally
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun MainScreen(viewModel: MainViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val level by viewModel.voiceLevel.collectAsStateWithLifecycle()
    var tab by rememberSaveable { mutableIntStateOf(0) }
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
            else -> ChatScreen(onOpenSetup = { tab = 1 })
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

private class NavItem(val label: String, @DrawableRes val icon: Int, @DrawableRes val selectedIcon: Int)

private val NavItems = listOf(
    NavItem("Focus", R.drawable.ic_center_focus_strong, R.drawable.ic_center_focus_strong_fill),
    NavItem("Space", R.drawable.ic_grid_view, R.drawable.ic_grid_view_fill),
    NavItem("Chat", R.drawable.ic_forum, R.drawable.ic_forum_fill),
)

/**
 * The app's frame: true black, the island under the camera, the current
 * tab, and a floating liquid-glass nav that the content scrolls under.
 */
@OptIn(ExperimentalLayoutApi::class)
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
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Oyi.Black)
                .glassSource(),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .statusBarsPadding()
                    .padding(top = 4.dp, bottom = 2.dp),
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
            Crossfade(
                targetState = tab,
                animationSpec = tween(durationMillis = 180),
                modifier = Modifier.weight(1f),
                label = "tab",
            ) { current ->
                Box(Modifier.fillMaxSize()) { content(current) }
            }
        }

        if (!WindowInsets.isImeVisible) {
            FloatingNav(
                selected = tab,
                onSelect = onTab,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .navigationBarsPadding()
                    .padding(bottom = NavBottomMargin),
            )
        }
    }
}

/**
 * Material 3 Expressive's short navigation bar, on liquid glass: the
 * selected destination grows a cream pill with its name; the others are
 * just their icons.
 */
@Composable
private fun FloatingNav(selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    val haptics = LocalHapticFeedback.current
    Row(
        modifier = modifier
            .height(NavHeight)
            .liquidGlass(Oyi.Pill, backdrop = LocalGlassBackdrop.current, elevation = 16.dp)
            .padding(6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        NavItems.forEachIndexed { index, item ->
            val isSelected = index == selected
            val bg by animateColorAsState(
                if (isSelected) Oyi.Cream else Oyi.Cream.copy(alpha = 0f),
                OyiMotion.defaultEffects(),
                label = "navBg",
            )
            val fg by animateColorAsState(
                if (isSelected) Oyi.Ink else Oyi.Muted,
                OyiMotion.defaultEffects(),
                label = "navFg",
            )
            PressMorph(
                onClick = {
                    if (!isSelected) haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                    onSelect(index)
                },
                modifier = Modifier.semantics {
                    role = Role.Tab
                    this.selected = isSelected
                },
            ) { shape ->
                Row(
                    modifier = Modifier
                        .height(NavHeight - 12.dp)
                        .background(bg, shape)
                        .padding(horizontal = if (isSelected) 18.dp else 20.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(
                        painterResource(if (isSelected) item.selectedIcon else item.icon),
                        contentDescription = item.label,
                        tint = fg,
                        modifier = Modifier.size(22.dp),
                    )
                    AnimatedVisibility(
                        visible = isSelected,
                        enter = fadeIn(OyiMotion.defaultEffects()) + expandHorizontally(OyiMotion.fastSpatial()),
                        exit = fadeOut(OyiMotion.fastEffects()) + shrinkHorizontally(OyiMotion.fastSpatial()),
                    ) {
                        Row {
                            Spacer(Modifier.width(8.dp))
                            Text(item.label, color = fg, fontSize = 14.sp, fontWeight = FontWeight.Bold, maxLines = 1)
                        }
                    }
                }
            }
        }
    }
}

/** The Space tab: access, screen time, the island and the gateway link. */
@Composable
fun SpaceContent(state: UiState, actions: SpaceActions) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = Gutter),
        verticalArrangement = Arrangement.spacedBy(CardGap),
    ) {
        item {
            ScreenTitle(title = "Space", subtitle = "How OYI lives on this phone.")
        }
        item { AccessCard(state, actions) }
        item { ScreenTimeCard(state) }
        item { IslandCard(state, actions) }
        item { IslandLookCard(state, actions) }
        item { GatewayCard(state, actions.saveGateway) }
        item { BottomClearance() }
    }
}

@Composable
private fun AccessCard(state: UiState, actions: SpaceActions) {
    val granted = listOf(state.overlayPermission, state.usagePermission, state.accessibilityEnabled).count { it }
    OyiCard(padding = PaddingValues(start = 20.dp, end = 16.dp, top = 20.dp, bottom = 10.dp)) {
        CardHeader(
            title = "Access",
            subtitle = if (granted == 3) "All set" else "$granted of 3 on",
            icon = R.drawable.ic_tune,
            iconTint = if (granted == 3) Oyi.Good else Oyi.Orange,
            iconBackground = if (granted == 3) Oyi.Good.copy(alpha = 0.14f) else Oyi.OrangeTint,
        )
        Spacer(Modifier.height(8.dp))
        AccessRow("Draw over apps", "Shows the island everywhere", R.drawable.ic_smartphone, state.overlayPermission, actions.openOverlay)
        RowDivider()
        AccessRow("Usage access", "Counts your screen time", R.drawable.ic_timer, state.usagePermission, actions.openUsage)
        RowDivider()
        AccessRow("Control access", "Blocks apps and works your phone", R.drawable.ic_visibility, state.accessibilityEnabled, actions.openAccessibility)
    }
}

@Composable
private fun AccessRow(title: String, detail: String, @DrawableRes icon: Int, granted: Boolean, onOpen: () -> Unit) {
    ListRow(
        title = title,
        subtitle = detail,
        leading = { IconTile(icon, size = 36.dp) },
        onClick = if (granted) null else onOpen,
    ) {
        if (granted) {
            Icon(painterResource(R.drawable.ic_check), contentDescription = "On", tint = Oyi.Good, modifier = Modifier.size(22.dp))
        } else {
            OyiButton(text = "Allow", onClick = onOpen, kind = ButtonKind.TONAL, small = true)
        }
    }
}

@Composable
private fun ScreenTimeCard(state: UiState) {
    OyiCard {
        CardHeader(
            title = "Screen time",
            subtitle = state.currentApp?.let { "Now in ${it.label}" } ?: "Today",
            icon = R.drawable.ic_timer,
        ) {
            val total = state.topApps.sumOf { it.totalMs }
            if (total > 0) {
                Text(
                    formatDuration(total),
                    style = MaterialTheme.typography.headlineSmall.merge(Tabular),
                    color = Oyi.OffWhite,
                )
            }
        }
        if (state.topApps.isEmpty()) {
            Spacer(Modifier.height(12.dp))
            Hint(if (state.usagePermission) "Nothing counted yet today." else "Allow usage access to see where your time goes.")
        } else {
            Spacer(Modifier.height(14.dp))
            val max = state.topApps.maxOf { it.totalMs }.coerceAtLeast(1L)
            state.topApps.take(4).forEachIndexed { index, app ->
                if (index > 0) Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    AppIcon(app.packageName, size = 32.dp, label = app.label)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(
                                app.label,
                                color = Oyi.OffWhite,
                                fontSize = 14.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                modifier = Modifier.weight(1f),
                            )
                            Text(
                                formatDuration(app.totalMs),
                                color = Oyi.Muted,
                                fontSize = 13.sp,
                                style = LocalTextStyle.current.merge(Tabular),
                            )
                        }
                        Spacer(Modifier.height(6.dp))
                        Box(Modifier.fillMaxWidth().height(4.dp).clip(Oyi.Pill).background(Oyi.Control)) {
                            Box(
                                Modifier
                                    .fillMaxWidth((app.totalMs.toFloat() / max).coerceIn(0.03f, 1f))
                                    .height(4.dp)
                                    .clip(Oyi.Pill)
                                    .background(if (index == 0) Oyi.Orange else Oyi.Muted),
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun IslandCard(state: UiState, actions: SpaceActions) {
    OyiCard(padding = PaddingValues(start = 20.dp, end = 16.dp, top = 20.dp, bottom = 20.dp)) {
        CardHeader(
            title = "Dynamic Island",
            subtitle = if (state.overlayEnabled) "On across apps" else "Off",
            icon = R.drawable.ic_bolt,
            iconTint = if (state.overlayEnabled) Oyi.Orange else Oyi.OffWhite,
            iconBackground = if (state.overlayEnabled) Oyi.OrangeTint else Oyi.Control,
        ) {
            OyiSwitch(state.overlayEnabled, actions.setOverlayEnabled)
        }
        Spacer(Modifier.height(8.dp))
        RowDivider(inset = 0.dp)
        ListRow(title = "Start when the phone starts") {
            OyiSwitch(state.autoStart, actions.setAutoStart)
        }
        RowDivider(inset = 0.dp)
        Spacer(Modifier.height(14.dp))
        Eyebrow("Try a state")
        Spacer(Modifier.height(10.dp))
        val previews = listOf(
            "Listening" to IslandKind.LISTENING,
            "Thinking" to IslandKind.THINKING,
            "Using a tool" to IslandKind.TOOL,
            "Speaking" to IslandKind.SPEAKING,
            "Working" to IslandKind.WORKING,
            "Approval" to IslandKind.APPROVAL_REQUIRED,
            "Done" to IslandKind.COMPLETED,
            "Idle" to IslandKind.IDLE,
        )
        Row(
            modifier = Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            previews.forEach { (label, kind) ->
                Pill(text = label) { actions.previewIsland(kind) }
            }
            Pill(text = "Two at once", onClick = actions.previewStack)
        }
    }
}

/** How the island looks and where it sits over the camera. */
@Composable
private fun IslandLookCard(state: UiState, actions: SpaceActions) {
    var x by remember(state.islandNudgeXDp) { mutableIntStateOf(state.islandNudgeXDp) }
    var y by remember(state.islandNudgeYDp) { mutableIntStateOf(state.islandNudgeYDp) }
    OyiCard(padding = PaddingValues(start = 20.dp, end = 16.dp, top = 20.dp, bottom = 12.dp)) {
        CardHeader(title = "Look and fit", subtitle = "Colour, Live Updates, camera fit", icon = R.drawable.ic_palette)
        Spacer(Modifier.height(8.dp))
        val dynamicOk = android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.S
        ListRow(
            title = "Match wallpaper colours",
            subtitle = if (dynamicOk) "Your Material You accent instead of OYI orange" else "Needs Android 12 or newer",
        ) {
            OyiSwitch(state.wallpaperColors, actions.setWallpaperColors)
        }
        RowDivider(inset = 0.dp)
        ListRow(
            title = "Live Update when hidden",
            subtitle = "A status bar chip on Android 16 while the island is off",
        ) {
            OyiSwitch(state.liveUpdates, actions.setLiveUpdates)
        }
        RowDivider(inset = 0.dp)
        ListRow(title = "Left or right", subtitle = "Nudge it over the camera") {
            Stepper(x, { x = it; actions.setIslandNudge(x, y) }, -24..24, unit = "")
        }
        ListRow(title = "Up or down") {
            Stepper(y, { y = it; actions.setIslandNudge(x, y) }, -24..24, unit = "")
        }
        AnimatedVisibility(visible = x != 0 || y != 0) {
            Box(Modifier.fillMaxWidth().padding(top = 4.dp, bottom = 6.dp), contentAlignment = Alignment.CenterEnd) {
                OyiButton(
                    text = "Reset position",
                    kind = ButtonKind.GHOST,
                    small = true,
                    onClick = {
                        x = 0
                        y = 0
                        actions.setIslandNudge(0, 0)
                    },
                )
            }
        }
    }
}

@Composable
private fun GatewayCard(state: UiState, onSave: (String, String) -> Unit) {
    var url by remember(state.gatewayUrl) { mutableStateOf(state.gatewayUrl) }
    var token by remember(state.token) { mutableStateOf(state.token) }
    val changed = url != state.gatewayUrl || token != state.token

    OyiCard {
        CardHeader(
            title = "Gateway",
            subtitle = if (state.deviceId.isNotBlank()) "This phone: ${state.deviceId.take(8)}" else "Links OYI to your agent",
            icon = R.drawable.ic_link,
        ) {
            ConnectionLabel(state.connection)
        }
        if (state.connectionError.isNotBlank() && state.connection == ConnectionState.ERROR) {
            Spacer(Modifier.height(12.dp))
            Text(state.connectionError, color = Oyi.Bad, fontSize = 13.sp, lineHeight = 18.sp, maxLines = 2)
        }
        Spacer(Modifier.height(16.dp))
        OyiTextField(
            value = url,
            onValueChange = { url = it.trim() },
            placeholder = "https://oyi.example.com",
            icon = R.drawable.ic_language,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        OyiTextField(
            value = token,
            onValueChange = { token = it.trim() },
            placeholder = "Pairing token",
            icon = R.drawable.ic_lock,
            password = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(14.dp))
        OyiButton(
            text = if (state.gatewayUrl.isBlank()) "Link OYI" else "Save changes",
            onClick = { onSave(url, token) },
            kind = if (changed && url.isNotBlank()) ButtonKind.PRIMARY else ButtonKind.TONAL,
            enabled = changed && url.isNotBlank(),
            fullWidth = true,
        )
    }
}

@Composable
private fun ConnectionLabel(connection: ConnectionState) {
    val (label, color) = when (connection) {
        ConnectionState.CONNECTED -> "Connected" to Oyi.Good
        ConnectionState.CONNECTING -> "Connecting" to Oyi.Warn
        ConnectionState.ERROR -> "Can't connect" to Oyi.Bad
        ConnectionState.DISCONNECTED -> "Offline" to Oyi.Faint
    }
    StatusLabel(label, color)
}

private fun formatDuration(millis: Long): String {
    val minutes = millis / 60_000
    val hours = minutes / 60
    val rest = minutes % 60
    return when {
        hours > 0 -> if (rest > 0) "${hours}h ${rest}m" else "${hours}h"
        minutes > 0 -> "${minutes}m"
        else -> "<1m"
    }
}
