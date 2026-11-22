package ai.oyi.device.ui

import ai.oyi.device.R
import androidx.compose.ui.semantics.onClick
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import ai.oyi.device.model.ConnectionState
import ai.oyi.device.model.IslandKind
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandAction
import ai.oyi.device.ui.island.IslandIdleInfo
import ai.oyi.device.ui.theme.GlassBackdrop
import ai.oyi.device.ui.theme.MorphingLoader
import ai.oyi.device.update.Release
import ai.oyi.device.update.UpdateState
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.layout
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.windowInsetsBottomHeight
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
    val update by viewModel.updates.collectAsStateWithLifecycle()
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
            openIslandFit = { context.startActivity(Intent(context, IslandFitActivity::class.java)) },
            checkUpdate = viewModel::checkForUpdate,
            downloadUpdate = viewModel::downloadUpdate,
            installUpdate = viewModel::installUpdate,
            saveGateway = viewModel::saveGateway,
        )
    }

    MainShell(
        state = state,
        level = level,
        tab = tab,
        onTab = { tab = it },
        badgeTab = if (update.isNews()) 2 else null,
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
            0 -> ChatScreen(onOpenSetup = { tab = 2 })
            1 -> FocusScreen(
                screenTime = ScreenTime(state.topApps, state.currentApp, state.usagePermission),
                onAllowUsage = space.openUsage,
            )
            else -> SpaceContent(state, space, update, viewModel.appVersion)
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
    val openIslandFit: () -> Unit = {},
    val checkUpdate: () -> Unit = {},
    val downloadUpdate: (Release) -> Unit = {},
    val installUpdate: (Release) -> Unit = {},
    val saveGateway: (String, String) -> Unit = { _, _ -> },
)

private class NavItem(val label: String, @DrawableRes val icon: Int, @DrawableRes val selectedIcon: Int)

// Chat first: it is what OYI is for. Space (settings) last.
private val NavItems = listOf(
    NavItem("Chat", R.drawable.ic_forum, R.drawable.ic_forum_fill),
    NavItem("Focus", R.drawable.ic_center_focus_strong, R.drawable.ic_center_focus_strong_fill),
    NavItem("Space", R.drawable.ic_grid_view, R.drawable.ic_grid_view_fill),
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
    badgeTab: Int? = null,
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
            // All three tabs are composed up front and stay alive; a switch
            // only lays out the one now shown, in the same frame as the tap.
            // No fade: a fade-in starts from black, and that black frame is
            // what made switching feel like it hesitated.
            Box(Modifier.weight(1f).fillMaxWidth()) {
                NavItems.indices.forEach { page ->
                    key(page) {
                        Box(if (page == tab) Modifier.fillMaxSize() else Modifier.dormant()) { content(page) }
                    }
                }
            }
        }

        if (!WindowInsets.isImeVisible) {
            // Content fades into the black under the nav instead of being cut.
            Box(
                Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .height(NavHeight + NavBottomMargin)
                    .background(Brush.verticalGradient(listOf(Color.Transparent, Oyi.Black.copy(alpha = 0.85f)))),
            )
            Box(
                Modifier
                    .align(Alignment.BottomCenter)
                    .fillMaxWidth()
                    .windowInsetsBottomHeight(WindowInsets.navigationBars)
                    .background(Oyi.Black.copy(alpha = 0.85f)),
            )
            FloatingNav(
                selected = tab,
                onSelect = onTab,
                badge = badgeTab,
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
private fun FloatingNav(selected: Int, onSelect: (Int) -> Unit, modifier: Modifier = Modifier, badge: Int? = null) {
    val haptics = LocalHapticFeedback.current
    Row(
        modifier = modifier
            .height(NavHeight)
            .liquidGlass(Oyi.Pill, fill = SolidColor(Color(0xE0161618)), backdrop = LocalGlassBackdrop.current, elevation = 16.dp)
            .padding(6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        NavItems.forEachIndexed { index, item ->
            val isSelected = index == selected
            // Effects springs this stiff settle in a frame or two: the colour
            // change reads as instant, without a hard cut.
            val bg by animateColorAsState(
                if (isSelected) Oyi.Cream else Oyi.Cream.copy(alpha = 0f),
                OyiMotion.fastEffects(),
                label = "navBg",
            )
            val fg by animateColorAsState(
                if (isSelected) Oyi.Ink else Oyi.Muted,
                OyiMotion.fastEffects(),
                label = "navFg",
            )
            var pressed by remember { mutableStateOf(false) }
            val squish by animateFloatAsState(if (pressed) 0.92f else 1f, OyiMotion.fastSpatial(), label = "navSquish")
            val select: () -> Unit by rememberUpdatedState({
                if (!isSelected) {
                    haptics.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                    onSelect(index)
                }
            })
            val shape = Oyi.Pill
            Box(
                modifier = Modifier
                    .graphicsLayer {
                        scaleX = squish
                        scaleY = squish
                    }
                    .clip(shape)
                    // Switch on touch-down, like a hardware key: the tab is
                    // already there by the time the finger lifts.
                    .pointerInput(index) {
                        detectTapGestures(
                            onPress = {
                                pressed = true
                                select()
                                tryAwaitRelease()
                                pressed = false
                            },
                        )
                    }
                    .semantics {
                        role = Role.Tab
                        this.selected = isSelected
                        onClick { select(); true }
                    },
            ) {
                Row(
                    modifier = Modifier
                        .height(NavHeight - 12.dp)
                        .background(bg, shape)
                        .padding(horizontal = if (isSelected) 18.dp else 20.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box {
                        Icon(
                            painterResource(if (isSelected) item.selectedIcon else item.icon),
                            contentDescription = item.label,
                            tint = fg,
                            modifier = Modifier.size(22.dp),
                        )
                        // Something new waits there (an update).
                        if (badge == index && !isSelected) {
                            Box(
                                Modifier
                                    .align(Alignment.TopEnd)
                                    .offset(x = 3.dp, y = (-2).dp)
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(Oyi.Orange),
                            )
                        }
                    }
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
fun SpaceContent(
    state: UiState,
    actions: SpaceActions,
    update: UpdateState = UpdateState.Idle,
    version: String = "",
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = Gutter),
        verticalArrangement = Arrangement.spacedBy(CardGap),
    ) {
        item {
            ScreenTitle(title = "Space", subtitle = "How OYI lives on this phone.")
        }
        // A waiting update goes first; otherwise it lives at the bottom.
        if (update.isNews()) item { UpdateCard(update, version, actions) }
        item { AccessCard(state, actions) }
        item { IslandCard(state, actions) }
        item { IslandLookCard(state, actions) }
        item { GatewayCard(state, actions.saveGateway) }
        if (!update.isNews()) item { UpdateCard(update, version, actions) }
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
        val x = state.islandNudgeXDp
        val y = state.islandNudgeYDp
        ListRow(
            title = "Fit over the camera",
            subtitle = if (x == 0 && y == 0) {
                "Centred on the camera"
            } else {
                listOfNotNull(
                    if (x != 0) "${kotlin.math.abs(x)} ${if (x > 0) "right" else "left"}" else null,
                    if (y != 0) "${kotlin.math.abs(y)} ${if (y > 0) "down" else "up"}" else null,
                ).joinToString(", ")
            },
            onClick = actions.openIslandFit,
        ) {
            OyiButton(text = "Adjust", onClick = actions.openIslandFit, kind = ButtonKind.TONAL, small = true)
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

/** An update the person should hear about. */
private fun UpdateState.isNews(): Boolean =
    this is UpdateState.Available || this is UpdateState.Downloading || this is UpdateState.Ready

@Composable
private fun UpdateCard(update: UpdateState, version: String, actions: SpaceActions) {
    OyiCard {
        val news = update.isNews()
        CardHeader(
            title = if (news) "Update ready" else "Updates",
            subtitle = when (update) {
                is UpdateState.Available -> "Build ${update.release.build} is out"
                is UpdateState.Downloading -> "Downloading build ${update.release.build}"
                is UpdateState.Ready -> "Build ${update.release.build} downloaded"
                else -> version.ifBlank { "OYI" }
            },
            icon = R.drawable.ic_system_update,
            iconTint = if (news) Oyi.Orange else Oyi.OffWhite,
            iconBackground = if (news) Oyi.OrangeTint else Oyi.Control,
        ) {
            when (update) {
                UpdateState.Checking -> MorphingLoader(color = Oyi.Muted, size = 20.dp)
                UpdateState.UpToDate -> StatusLabel("Latest", Oyi.Good)
                else -> Unit
            }
        }
        val notes = when (update) {
            is UpdateState.Available -> update.release.notes
            is UpdateState.Ready -> update.release.notes
            else -> ""
        }
        if (notes.isNotBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(notes, color = Oyi.Muted, fontSize = 14.sp, lineHeight = 20.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
        }
        if (update is UpdateState.Failed) {
            Spacer(Modifier.height(12.dp))
            Text(update.message, color = Oyi.Bad, fontSize = 13.sp, lineHeight = 18.sp)
        }
        Spacer(Modifier.height(16.dp))
        when (update) {
            is UpdateState.Available -> OyiButton(
                text = "Download and install",
                onClick = { actions.downloadUpdate(update.release) },
                icon = R.drawable.ic_download,
                fullWidth = true,
            )
            is UpdateState.Downloading -> {
                Box(Modifier.fillMaxWidth().height(8.dp).clip(Oyi.Pill).background(Oyi.Control)) {
                    Box(
                        Modifier
                            .fillMaxWidth(update.progress.coerceIn(0.02f, 1f))
                            .height(8.dp)
                            .clip(Oyi.Pill)
                            .background(Oyi.Orange),
                    )
                }
                Spacer(Modifier.height(8.dp))
                Text(
                    "${(update.progress * 100).toInt()}%",
                    color = Oyi.Muted,
                    fontSize = 13.sp,
                    style = LocalTextStyle.current.merge(Tabular),
                )
            }
            is UpdateState.Ready -> OyiButton(
                text = "Install now",
                onClick = { actions.installUpdate(update.release) },
                icon = R.drawable.ic_system_update,
                fullWidth = true,
            )
            UpdateState.Installing -> OyiButton(text = "Installing", onClick = {}, kind = ButtonKind.TONAL, enabled = false, fullWidth = true)
            UpdateState.Checking -> OyiButton(text = "Checking", onClick = {}, kind = ButtonKind.TONAL, enabled = false, fullWidth = true)
            else -> OyiButton(
                text = "Check for updates",
                onClick = actions.checkUpdate,
                kind = ButtonKind.TONAL,
                fullWidth = true,
            )
        }
    }
}

/** Laid out as nothing: not measured, drawn or hit-tested, yet still composed. */
private fun Modifier.dormant(): Modifier = layout { _, _ -> layout(0, 0) {} }
