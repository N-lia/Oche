package ai.oyi.device.ui

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
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
import ai.oyi.device.model.IslandState
import ai.oyi.device.ui.island.DynamicIsland
import ai.oyi.device.ui.island.IslandAction
import ai.oyi.device.ui.island.IslandIdleInfo

private val PitchBlack = Color(0xFF070708)
private val SoftDarkCard = Color(0xCC1A1A1F)
private val SoftDarkInner = Color(0x992A2A32)
private val OffWhite = Color(0xFFF8F6F1)
private val MutedWarmGrey = Color(0xFFA09C94)
private val AgentOrange = Color(0xFFF08A65)
private val AgentOrangeTop = Color(0xFFFFA584)
private val AgentOrangeBottom = Color(0xFFE07450)
private val AgentOrangeGlassPill = Color(0x260A0807)
private val UserCream = Color(0xFFF7EDD0)
private val UserCreamTop = Color(0xFFFFF8E6)
private val WarmYellow = Color(0xFFF7E27E)
private val WarmYellowTop = Color(0xFFFFF0A6)
private val DarkInk = Color(0xFF0A0807)
private val SecondaryInk = Color(0xFF1C120E)

private val DarkGlassRim = Brush.verticalGradient(
    colors = listOf(Color(0x59FFFFFF), Color(0x14FFFFFF)),
)
private val WarmGlassRim = Brush.verticalGradient(
    colors = listOf(Color(0xB3FFF9F2), Color(0x2EFFFFFF)),
)
private val OrangeGlassBrush = Brush.linearGradient(
    colors = listOf(AgentOrangeTop, AgentOrange, AgentOrangeBottom),
)
private val CreamGlassBrush = Brush.linearGradient(
    colors = listOf(UserCreamTop, UserCream),
)
private val YellowGlassBrush = Brush.linearGradient(
    colors = listOf(WarmYellowTop, WarmYellow),
)
private val SmokedGlassBrush = Brush.linearGradient(
    colors = listOf(Color(0xD926262D), Color(0xCC141418)),
)

@Composable
fun MainScreen(viewModel: MainViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var tab by remember { mutableStateOf(0) }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) viewModel.refresh()
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    Scaffold(
        containerColor = PitchBlack,
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(PitchBlack)
                .padding(padding)
        ) {
            val level by viewModel.voiceLevel.collectAsStateWithLifecycle()
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
                    onAction = { action ->
                        when (action) {
                            IslandAction.APPROVE -> viewModel.approveIsland()
                            IslandAction.DENY -> viewModel.denyIsland()
                            IslandAction.STOP -> viewModel.stopActiveTask()
                            IslandAction.DISMISS -> viewModel.dismissIsland()
                            IslandAction.OPEN_APP -> Unit
                        }
                    },
                    modifier = Modifier.padding(horizontal = 16.dp),
                )
            }

            MinimalPillTabs(
                selectedTab = tab,
                onSelectTab = { tab = it },
            )

            when (tab) {
                0 -> FocusScreen()
                1 -> SetupList(state = state, viewModel = viewModel)
                else -> ChatScreen()
            }
        }
    }
}

@Composable
private fun MinimalPillTabs(
    selectedTab: Int,
    onSelectTab: (Int) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val tabs = listOf("Focus", "Space", "Live Chat")
        tabs.forEachIndexed { index, title ->
            val selected = selectedTab == index
            Surface(
                color = if (selected) Color(0x24FFFFFF) else Color(0x12FFFFFF),
                shape = RoundedCornerShape(999.dp),
                border = BorderStroke(
                    1.dp,
                    if (selected) WarmGlassRim else DarkGlassRim,
                ),
                modifier = Modifier.clickable { onSelectTab(index) },
            ) {
                Text(
                    text = title,
                    color = if (selected) OffWhite else MutedWarmGrey,
                    fontSize = 13.5.sp,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                    modifier = Modifier.padding(horizontal = 18.dp, vertical = 9.dp),
                )
            }
        }
    }
}

@Composable
private fun SetupList(state: UiState, viewModel: MainViewModel) {
    val context = LocalContext.current
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
                fontSize = 36.sp,
                lineHeight = 40.sp,
                color = OffWhite,
                fontWeight = FontWeight.Light,
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
                        onOpenOverlay = {
                            context.startActivity(
                                Intent(
                                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                    Uri.parse("package:${context.packageName}"),
                                ),
                            )
                        },
                        onOpenUsage = {
                            context.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                        },
                        onOpenAccessibility = {
                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                        },
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
                onToggle = { viewModel.setOverlayEnabled(it) },
                onAutoStart = { viewModel.setAutoStart(it) },
                onPreviewIsland = { viewModel.previewIslandState(it) },
            )
        }

        item {
            GatewayCard(
                state = state,
                onSave = { url, token -> viewModel.saveGateway(url, token) },
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
    val shape = RoundedCornerShape(28.dp)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(12.dp, shape),
        shape = shape,
        border = BorderStroke(1.dp, WarmGlassRim),
        colors = CardDefaults.cardColors(containerColor = Color.Transparent),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(OrangeGlassBrush)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = "Device\nAccess",
                fontSize = 18.5.sp,
                lineHeight = 22.sp,
                color = DarkInk,
                fontWeight = FontWeight.Bold,
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
    Surface(
        color = AgentOrangeGlassPill,
        shape = RoundedCornerShape(999.dp),
        border = BorderStroke(0.8.dp, Color(0x47FFFFFF)),
        modifier = Modifier
            .fillMaxWidth()
            .clickable { if (!granted) onClick() },
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(18.dp)
                    .clip(CircleShape)
                    .background(if (granted) DarkInk else Color(0x260A0807)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = if (granted) "✓" else "○",
                    color = if (granted) Color(0xFFFFF8E7) else DarkInk,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                text = label,
                color = DarkInk,
                fontSize = 12.5.sp,
                fontWeight = FontWeight.Bold,
            )
        }
    }
}

@Composable
private fun UsageBentoCard(state: UiState) {
    val shape = RoundedCornerShape(28.dp)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(12.dp, shape),
        shape = shape,
        border = BorderStroke(1.dp, WarmGlassRim),
        colors = CardDefaults.cardColors(containerColor = Color.Transparent),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(YellowGlassBrush)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                text = "Screen\nTime",
                fontSize = 18.5.sp,
                lineHeight = 22.sp,
                color = DarkInk,
                fontWeight = FontWeight.Bold,
            )
            Text(
                text = state.currentApp?.let { "Now · ${it.label}" } ?: "Updated live",
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = Color(0xFF2E2810),
            )
            Spacer(Modifier.height(4.dp))
            if (state.topApps.isEmpty()) {
                Text(
                    text = "No usage yet",
                    fontSize = 12.sp,
                    color = Color(0xFF2E2810),
                )
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
                            color = DarkInk,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        Text(
                            text = formatDuration(app.totalMs),
                            fontSize = 11.5.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                            color = DarkInk,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun RunCard(
    enabled: Boolean,
    autoStart: Boolean,
    onToggle: (Boolean) -> Unit,
    onAutoStart: (Boolean) -> Unit,
    onPreviewIsland: (IslandKind) -> Unit,
) {
    val shape = RoundedCornerShape(28.dp)
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(12.dp, shape),
        shape = shape,
        border = BorderStroke(1.dp, WarmGlassRim),
        colors = CardDefaults.cardColors(containerColor = Color.Transparent),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(CreamGlassBrush)
                .padding(18.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = if (enabled) "Active across apps" else "Stopped",
                        fontSize = 11.5.sp,
                        fontWeight = FontWeight.Medium,
                        color = Color(0xFF3D3624),
                    )
                    Text(
                        text = "Dynamic Island",
                        fontSize = 18.5.sp,
                        color = DarkInk,
                        fontWeight = FontWeight.Bold,
                    )
                }
                Switch(
                    checked = enabled,
                    onCheckedChange = onToggle,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = UserCream,
                        checkedTrackColor = DarkInk,
                        uncheckedThumbColor = DarkInk,
                        uncheckedTrackColor = Color(0xFFDAD0B2),
                    ),
                )
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "Launch on boot",
                    fontSize = 13.5.sp,
                    fontWeight = FontWeight.Medium,
                    color = DarkInk,
                    modifier = Modifier.weight(1f),
                )
                Switch(
                    checked = autoStart,
                    onCheckedChange = onAutoStart,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = UserCream,
                        checkedTrackColor = DarkInk,
                        uncheckedThumbColor = DarkInk,
                        uncheckedTrackColor = Color(0xFFDAD0B2),
                    ),
                )
            }
            Spacer(Modifier.height(10.dp))
            Text(
                text = "Preview the island",
                fontSize = 11.5.sp,
                fontWeight = FontWeight.Medium,
                color = Color(0xFF3D3624),
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
                        Surface(
                            color = Color(0x1F0A0807),
                            shape = RoundedCornerShape(999.dp),
                            border = BorderStroke(0.8.dp, Color(0x66FFFFFF)),
                            modifier = Modifier
                                .weight(1f)
                                .clickable { onPreviewIsland(kind) },
                        ) {
                            Box(
                                modifier = Modifier.padding(vertical = 8.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(
                                    text = label,
                                    color = DarkInk,
                                    fontSize = 11.5.sp,
                                    fontWeight = FontWeight.Bold,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun GatewayCard(
    state: UiState,
    onSave: (String, String) -> Unit,
) {
    var url by remember(state.gatewayUrl) { mutableStateOf(state.gatewayUrl) }
    var token by remember(state.token) { mutableStateOf(state.token) }
    val shape = RoundedCornerShape(28.dp)

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .shadow(14.dp, shape),
        shape = shape,
        border = BorderStroke(1.dp, DarkGlassRim),
        colors = CardDefaults.cardColors(containerColor = Color.Transparent),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .background(SmokedGlassBrush)
                .padding(18.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Gateway Link",
                    fontSize = 17.sp,
                    color = OffWhite,
                    fontWeight = FontWeight.Medium,
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
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("Pairing token") },
                singleLine = true,
                shape = RoundedCornerShape(16.dp),
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Button(
                    onClick = { onSave(url, token) },
                    shape = RoundedCornerShape(999.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = UserCream,
                        contentColor = DarkInk,
                    ),
                ) {
                    Text("Save Link", fontWeight = FontWeight.SemiBold)
                }
                Spacer(Modifier.weight(1f))
                if (state.deviceId.isNotBlank()) {
                    Text(
                        text = state.deviceId.take(8),
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = MutedWarmGrey,
                    )
                }
            }
        }
    }
}

@Composable
private fun ConnectionBadge(connection: ConnectionState, error: String) {
    val (label, color) = when (connection) {
        ConnectionState.CONNECTED -> "Connected" to UserCream
        ConnectionState.CONNECTING -> "Connecting" to WarmYellow
        ConnectionState.ERROR -> "Error" to AgentOrange
        ConnectionState.DISCONNECTED -> "Offline" to MutedWarmGrey
    }
    Column(horizontalAlignment = Alignment.End) {
        Text(label, color = color, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.SemiBold)
        if (error.isNotBlank()) {
            Text(
                text = error.take(40),
                style = MaterialTheme.typography.labelSmall,
                color = MutedWarmGrey,
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
