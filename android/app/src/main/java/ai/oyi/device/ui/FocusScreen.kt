package ai.oyi.device.ui

import ai.oyi.device.R
import ai.oyi.device.ui.theme.MorphingLoader
import ai.oyi.device.ui.theme.OyiMotion
import ai.oyi.device.ui.theme.TabularNumbers
import android.content.Intent
import android.provider.Settings
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import ai.oyi.device.focus.Distraction
import ai.oyi.device.focus.DriftState
import ai.oyi.device.focus.FocusLive
import ai.oyi.device.focus.FocusSession
import ai.oyi.device.focus.FocusState
import ai.oyi.device.focus.LIMIT_BLOCKED
import ai.oyi.device.focus.LIMIT_FOCUS_ONLY
import ai.oyi.device.focus.LockPhase
import ai.oyi.device.focus.LockStatus
import ai.oyi.device.focus.Voice
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

private val LIMIT_OPTIONS = listOf(LIMIT_BLOCKED, 15, 30, 60, 120, LIMIT_FOCUS_ONLY)
private val SESSION_LENGTHS = listOf(25, 50, 90)

private fun limitLabel(minutes: Int): String = when {
    minutes == LIMIT_BLOCKED -> "Blocked"
    minutes < 0 -> "Focus only"
    minutes % 60 == 0 -> "${minutes / 60}h a day"
    else -> "${minutes}m a day"
}

/** Everything the Focus screen can ask for. The defaults do nothing, for previews. */
class FocusActions(
    val label: (String) -> String = { it },
    val clearNotice: () -> Unit = {},
    val loadApps: () -> Unit = {},
    val startSession: (String, Int) -> Unit = { _, _ -> },
    val finishSession: () -> Unit = {},
    val setDistraction: (String, Int) -> Unit = { _, _ -> },
    val removeDistraction: (String) -> Unit = {},
    val addSite: (String) -> Boolean = { false },
    val removeSite: (String) -> Unit = {},
    val armLock: () -> Unit = {},
    val disarmLock: () -> Unit = {},
    val requestEmergencyExit: () -> Unit = {},
    val cancelEmergencyExit: () -> Unit = {},
    val setVoice: (Voice) -> Unit = {},
    val openAccessibility: () -> Unit = {},
)

@Composable
fun FocusScreen(viewModel: FocusViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val live by viewModel.live.collectAsStateWithLifecycle()
    val accessibilityReady by viewModel.accessibilityReady.collectAsStateWithLifecycle()
    val notice by viewModel.notice.collectAsStateWithLifecycle()
    val apps by viewModel.apps.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val actions = remember(viewModel) {
        FocusActions(
            label = viewModel::label,
            clearNotice = viewModel::clearNotice,
            loadApps = viewModel::loadApps,
            startSession = viewModel::startSession,
            finishSession = viewModel::finishSession,
            setDistraction = viewModel::setDistraction,
            removeDistraction = viewModel::removeDistraction,
            addSite = viewModel::addSite,
            removeSite = viewModel::removeSite,
            armLock = viewModel::armLock,
            disarmLock = viewModel::disarmLock,
            requestEmergencyExit = viewModel::requestEmergencyExit,
            cancelEmergencyExit = viewModel::cancelEmergencyExit,
            setVoice = viewModel::setVoice,
            openAccessibility = {
                context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            },
        )
    }
    FocusContent(state, live, accessibilityReady, notice, apps, actions)
}

/** The Focus screen itself, without a view model (previews and snapshots use it). */
@Composable
fun FocusContent(
    state: FocusState,
    live: FocusLive,
    accessibilityReady: Boolean,
    notice: String,
    apps: List<AppEntry>,
    actions: FocusActions,
) {
    var showPicker by remember { mutableStateOf(false) }

    LaunchedEffect(notice) {
        if (notice.isNotEmpty()) {
            delay(3500)
            actions.clearNotice()
        }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = Gutter),
        verticalArrangement = Arrangement.spacedBy(CardGap),
    ) {
        item {
            ScreenTitle(
                title = "Focus",
                subtitle = when {
                    state.session != null -> "One task. Everything else waits."
                    else -> "Pick one task and block the noise."
                },
            )
            AnimatedVisibility(
                visible = notice.isNotEmpty(),
                enter = fadeIn(OyiMotion.defaultEffects()) + expandVertically(OyiMotion.defaultSpatial()),
                exit = fadeOut(OyiMotion.fastEffects()) + shrinkVertically(OyiMotion.defaultSpatial()),
            ) {
                Row(
                    modifier = Modifier
                        .padding(bottom = 4.dp)
                        .clip(Oyi.Pill)
                        .background(Oyi.OrangeTint)
                        .padding(horizontal = 14.dp, vertical = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Icon(painterResource(R.drawable.ic_bolt), null, tint = Oyi.Orange, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(8.dp))
                    Text(notice, color = Oyi.Orange, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }

        if (!accessibilityReady) {
            item {
                OyiCard {
                    CardHeader(
                        title = "Blocking is asleep",
                        subtitle = "Needs Control access",
                        icon = R.drawable.ic_block,
                        iconTint = Oyi.Orange,
                        iconBackground = Oyi.OrangeTint,
                    )
                    Spacer(Modifier.height(12.dp))
                    Text(
                        "OYI uses Control access to see which app is open and to cover the ones you block.",
                        color = Oyi.Muted,
                        fontSize = 14.sp,
                        lineHeight = 20.sp,
                    )
                    Spacer(Modifier.height(16.dp))
                    OyiButton(
                        text = "Turn on Control access",
                        onClick = actions.openAccessibility,
                        fullWidth = true,
                    )
                }
            }
        }

        item {
            val session = state.session
            if (session == null) {
                StartBlockCard(onStart = actions.startSession)
            } else {
                ActiveBlockCard(session = session, live = live, onDone = actions.finishSession)
            }
        }

        item {
            DistractionsCard(
                state = state,
                live = live,
                labelOf = actions.label,
                onAdd = {
                    actions.loadApps()
                    showPicker = true
                },
                onSetLimit = actions.setDistraction,
                onRemove = actions.removeDistraction,
            )
        }

        item {
            SitesCard(
                sites = state.sites,
                canLoosen = live.lock.canLoosen,
                onAdd = actions.addSite,
                onRemove = actions.removeSite,
            )
        }

        item {
            LockCard(
                status = live.lock,
                now = live.now,
                onArm = actions.armLock,
                onDisarm = actions.disarmLock,
                onEmergency = actions.requestEmergencyExit,
                onCancelEmergency = actions.cancelEmergencyExit,
            )
        }

        item { VoiceCard(voice = state.voice, onPick = actions.setVoice) }

        item { BottomClearance() }
    }

    if (showPicker) {
        val taken = state.distractions.map { it.packageName }.toSet()
        AppPicker(
            apps = apps.filter { it.packageName !in taken },
            onPick = { entry ->
                actions.setDistraction(entry.packageName, LIMIT_FOCUS_ONLY)
                showPicker = false
            },
            onDismiss = { showPicker = false },
        )
    }
}

@Composable
private fun StartBlockCard(onStart: (String, Int) -> Unit) {
    var task by rememberSaveable { mutableStateOf("") }
    var minutes by rememberSaveable { mutableStateOf(SESSION_LENGTHS.first()) }
    val focus = LocalFocusManager.current

    OyiCard(padding = PaddingValues(start = 20.dp, end = 20.dp, top = 18.dp, bottom = 20.dp)) {
        Eyebrow("Next block")
        Spacer(Modifier.height(10.dp))
        BasicTextField(
            value = task,
            onValueChange = { task = it.take(80) },
            singleLine = true,
            textStyle = MaterialTheme.typography.headlineSmall.copy(color = Oyi.OffWhite, fontWeight = FontWeight.Medium),
            cursorBrush = SolidColor(Oyi.Orange),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done, capitalization = KeyboardCapitalization.Sentences),
            keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
            modifier = Modifier.fillMaxWidth(),
            decorationBox = { inner ->
                Box {
                    if (task.isEmpty()) {
                        Text(
                            "What will you work on?",
                            style = MaterialTheme.typography.headlineSmall,
                            fontWeight = FontWeight.Medium,
                            color = Oyi.Faint,
                        )
                    }
                    inner()
                }
            },
        )
        Spacer(Modifier.height(18.dp))
        SegmentedButtons(
            options = SESSION_LENGTHS.map { "$it min" },
            selected = SESSION_LENGTHS.indexOf(minutes).coerceAtLeast(0),
            onSelect = { minutes = SESSION_LENGTHS[it] },
        )
        Spacer(Modifier.height(12.dp))
        OyiButton(
            text = "Start $minutes-minute block",
            onClick = {
                focus.clearFocus()
                onStart(task, minutes)
                task = ""
            },
            fullWidth = true,
        )
        Spacer(Modifier.height(14.dp))
        Hint("Drift into a distraction and OYI nudges you twice. After that it locks them until the block ends.")
    }
}

@Composable
private fun ActiveBlockCard(session: FocusSession, live: FocusLive, onDone: () -> Unit) {
    val total = (session.endsAt - session.startedAt).coerceAtLeast(1L)
    val remaining = (session.endsAt - live.now).coerceIn(0L, total)
    val progress = 1f - remaining.toFloat() / total.toFloat()
    val (status, statusColor) = when (live.drift.stage) {
        DriftState.STAGE_LOCKDOWN -> "Locked down" to Oyi.Bad
        DriftState.STAGE_FIRM -> "Drifting, last warning" to Oyi.Orange
        DriftState.STAGE_GENTLE -> "Drifting" to Oyi.Warn
        else -> "On track" to Oyi.Good
    }

    OyiCard(padding = PaddingValues(start = 20.dp, end = 20.dp, top = 28.dp, bottom = 20.dp)) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            FocusRing(progress = progress, modifier = Modifier.size(216.dp), strokeWidth = 10.dp) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = formatClock(remaining),
                        style = MaterialTheme.typography.displayMedium,
                        fontSize = 48.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = Oyi.OffWhite,
                        fontFeatureSettings = TabularNumbers,
                    )
                    Text(
                        "of ${total / 60_000L} min",
                        color = Oyi.Muted,
                        fontSize = 13.sp,
                        fontFeatureSettings = TabularNumbers,
                    )
                }
            }
            Spacer(Modifier.height(22.dp))
            Text(
                text = session.task.ifBlank { "Deep work" },
                style = MaterialTheme.typography.titleLarge,
                color = Oyi.OffWhite,
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(8.dp))
            StatusLabel(status, statusColor)
            Spacer(Modifier.height(22.dp))
            OyiButton(
                text = "I finished this",
                onClick = onDone,
                kind = ButtonKind.CREAM,
                icon = R.drawable.ic_check,
                fullWidth = true,
            )
        }
    }
}

@Composable
private fun DistractionsCard(
    state: FocusState,
    live: FocusLive,
    labelOf: (String) -> String,
    onAdd: () -> Unit,
    onSetLimit: (String, Int) -> Unit,
    onRemove: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf("") }
    val count = state.distractions.size

    OyiCard {
        CardHeader(
            title = "Distractions",
            subtitle = when (count) {
                0 -> "None yet"
                1 -> "1 app"
                else -> "$count apps"
            },
            icon = R.drawable.ic_apps,
        ) {
            OyiButton(text = "Add", onClick = onAdd, kind = ButtonKind.TONAL, icon = R.drawable.ic_add, small = true)
        }

        if (state.distractions.isEmpty()) {
            Spacer(Modifier.height(12.dp))
            Hint("Pick the apps that pull you away. Give each a daily allowance, or block it outright.")
        } else {
            Spacer(Modifier.height(8.dp))
        }

        state.distractions.forEachIndexed { index, item ->
            if (index > 0) RowDivider(inset = 54.dp)
            DistractionRow(
                item = item,
                label = labelOf(item.packageName),
                usedMs = live.usedToday[item.packageName] ?: 0L,
                open = expanded == item.packageName,
                canLoosen = live.lock.canLoosen,
                onToggle = {
                    expanded = if (expanded == item.packageName) "" else item.packageName
                },
                onSetLimit = { onSetLimit(item.packageName, it) },
                onRemove = { onRemove(item.packageName) },
            )
        }
    }
}

@Composable
private fun DistractionRow(
    item: Distraction,
    label: String,
    usedMs: Long,
    open: Boolean,
    canLoosen: Boolean,
    onToggle: () -> Unit,
    onSetLimit: (Int) -> Unit,
    onRemove: () -> Unit,
) {
    val detail = when {
        item.dailyLimitMin == LIMIT_BLOCKED -> "Always blocked"
        item.dailyLimitMin < 0 -> "Blocked when you drift"
        else -> "${usedMs / 60_000L} of ${item.dailyLimitMin} min today"
    }
    val chevron by animateFloatAsState(if (open) 90f else 0f, OyiMotion.fastSpatial(), label = "chevron")
    Column {
        ListRow(
            title = label,
            subtitle = detail,
            leading = { AppIcon(item.packageName, label = label) },
            onClick = onToggle,
        ) {
            Icon(
                painterResource(R.drawable.ic_chevron_right),
                contentDescription = if (open) "Close" else "Edit",
                tint = Oyi.Faint,
                modifier = Modifier.size(22.dp).rotate(chevron),
            )
        }
        AnimatedVisibility(
            visible = open,
            enter = fadeIn(OyiMotion.defaultEffects()) + expandVertically(OyiMotion.defaultSpatial()),
            exit = fadeOut(OyiMotion.fastEffects()) + shrinkVertically(OyiMotion.defaultSpatial()),
        ) {
            Column(Modifier.padding(start = 54.dp, bottom = 12.dp)) {
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    LIMIT_OPTIONS.forEach { option ->
                        val looser = Distraction(item.packageName, option).looseness > item.looseness
                        Pill(
                            text = limitLabel(option),
                            selected = option == item.dailyLimitMin,
                            enabled = canLoosen || !looser,
                            onClick = { onSetLimit(option) },
                        )
                    }
                }
                Spacer(Modifier.height(10.dp))
                OyiButton(
                    text = "Remove",
                    onClick = onRemove,
                    kind = ButtonKind.DANGER,
                    icon = R.drawable.ic_close,
                    enabled = canLoosen,
                    small = true,
                )
            }
        }
    }
}

@Composable
private fun SitesCard(
    sites: List<String>,
    canLoosen: Boolean,
    onAdd: (String) -> Boolean,
    onRemove: (String) -> Unit,
) {
    var draft by rememberSaveable { mutableStateOf("") }
    val focus = LocalFocusManager.current
    val submit = {
        if (draft.isNotBlank() && onAdd(draft)) {
            draft = ""
            focus.clearFocus()
        }
    }

    OyiCard {
        CardHeader(
            title = "Blocked sites",
            subtitle = if (sites.isEmpty()) "None yet" else if (sites.size == 1) "1 site" else "${sites.size} sites",
            icon = R.drawable.ic_language,
        )
        Spacer(Modifier.height(16.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OyiTextField(
                value = draft,
                onValueChange = { draft = it.take(120) },
                placeholder = "youtube.com",
                icon = R.drawable.ic_link,
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { submit() }),
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(8.dp))
            OyiButton(
                text = "Block",
                onClick = { submit() },
                kind = if (draft.isNotBlank()) ButtonKind.PRIMARY else ButtonKind.TONAL,
                enabled = draft.isNotBlank(),
            )
        }
        if (sites.isNotEmpty()) Spacer(Modifier.height(6.dp))
        sites.forEachIndexed { index, site ->
            if (index > 0) RowDivider(inset = 0.dp)
            Row(
                modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    site,
                    color = Oyi.OffWhite,
                    fontSize = 15.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    icon = R.drawable.ic_close,
                    contentDescription = "Unblock $site",
                    onClick = { onRemove(site) },
                    enabled = canLoosen,
                )
            }
        }
        Spacer(Modifier.height(12.dp))
        Hint("Works in Chrome, Firefox, Brave, Edge, Opera, Samsung Internet and DuckDuckGo.")
    }
}

@Composable
private fun LockCard(
    status: LockStatus,
    now: Long,
    onArm: () -> Unit,
    onDisarm: () -> Unit,
    onEmergency: () -> Unit,
    onCancelEmergency: () -> Unit,
) {
    var confirmArm by remember { mutableStateOf(false) }
    var confirmExit by remember { mutableStateOf(false) }
    LaunchedEffect(confirmArm, confirmExit) {
        if (confirmArm || confirmExit) {
            delay(4000)
            confirmArm = false
            confirmExit = false
        }
    }
    val left = formatSpan(status.until - now)
    val armed = status.phase != LockPhase.OFF

    OyiCard {
        val (headline, label, labelColor) = when (status.phase) {
            LockPhase.OFF -> Triple("Monthly lock", "Off", Oyi.Faint)
            LockPhase.LOCKED -> Triple("Monthly lock", "Locked", Oyi.Orange)
            LockPhase.WINDOW_OPEN -> Triple("Monthly lock", "Window open", Oyi.Good)
            LockPhase.EMERGENCY_WAIT -> Triple("Monthly lock", "Exit requested", Oyi.Warn)
            LockPhase.EMERGENCY_OPEN -> Triple("Monthly lock", "Exit open", Oyi.Good)
        }
        CardHeader(
            title = headline,
            icon = if (status.phase == LockPhase.LOCKED || status.phase == LockPhase.EMERGENCY_WAIT) R.drawable.ic_lock else R.drawable.ic_lock_open,
            iconTint = if (armed) Oyi.Orange else Oyi.OffWhite,
            iconBackground = if (armed) Oyi.OrangeTint else Oyi.Control,
        ) {
            StatusLabel(label, labelColor)
        }
        Spacer(Modifier.height(12.dp))
        val body = when (status.phase) {
            LockPhase.OFF ->
                "You can still add blocks, but removing or relaxing them only opens on the 1st of each month."
            LockPhase.LOCKED ->
                if (status.needsSync) {
                    "Today is the window. Go online once so OYI can check the date."
                } else {
                    "Opens ${formatDay(status.until)}, in $left. Until then you can only tighten."
                }
            LockPhase.WINDOW_OPEN -> "Closes in $left. Relax what you need, or turn the lock off."
            LockPhase.EMERGENCY_WAIT -> "The lock opens for one hour in $left."
            LockPhase.EMERGENCY_OPEN -> "Closes in $left. Change what you must."
        }
        Text(body, color = Oyi.Muted, fontSize = 14.sp, lineHeight = 20.sp)

        if (status.phase == LockPhase.LOCKED && !status.needsSync) {
            Spacer(Modifier.height(16.dp))
            MonthBar(now = now, opens = status.until)
        }

        Spacer(Modifier.height(16.dp))
        when (status.phase) {
            LockPhase.OFF -> OyiButton(
                text = if (confirmArm) "Tap again to lock" else "Turn on lock",
                kind = if (confirmArm) ButtonKind.PRIMARY else ButtonKind.TONAL,
                icon = R.drawable.ic_lock,
                fullWidth = true,
                onClick = {
                    if (confirmArm) {
                        confirmArm = false
                        onArm()
                    } else {
                        confirmArm = true
                    }
                },
            )
            LockPhase.LOCKED -> OyiButton(
                text = if (confirmExit) "Tap again: wait 24 hours" else "Emergency exit",
                kind = if (confirmExit) ButtonKind.DANGER else ButtonKind.GHOST,
                fullWidth = true,
                onClick = {
                    if (confirmExit) {
                        confirmExit = false
                        onEmergency()
                    } else {
                        confirmExit = true
                    }
                },
            )
            LockPhase.WINDOW_OPEN, LockPhase.EMERGENCY_OPEN -> OyiButton(
                text = "Turn off lock",
                kind = ButtonKind.TONAL,
                icon = R.drawable.ic_lock_open,
                fullWidth = true,
                onClick = onDisarm,
            )
            LockPhase.EMERGENCY_WAIT -> OyiButton(
                text = "Cancel exit",
                kind = ButtonKind.TONAL,
                fullWidth = true,
                onClick = onCancelEmergency,
            )
        }
    }
}

/** How far through the locked month we are. */
@Composable
private fun MonthBar(now: Long, opens: Long) {
    val zone = ZoneId.systemDefault()
    val start = Instant.ofEpochMilli(opens).atZone(zone).toLocalDate().minusMonths(1)
        .atStartOfDay(zone).toInstant().toEpochMilli()
    val span = (opens - start).coerceAtLeast(1L)
    val fraction = ((now - start).toFloat() / span.toFloat()).coerceIn(0.02f, 1f)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(6.dp)
            .clip(Oyi.Pill)
            .background(Oyi.Control),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(fraction)
                .height(6.dp)
                .clip(Oyi.Pill)
                .background(Oyi.Orange),
        )
    }
}

@Composable
private fun VoiceCard(voice: Voice, onPick: (Voice) -> Unit) {
    OyiCard {
        CardHeader(
            title = "Voice",
            subtitle = "How OYI talks when you drift",
            icon = R.drawable.ic_graphic_eq,
        )
        Spacer(Modifier.height(16.dp))
        Row(
            modifier = Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Voice.values().forEach { option ->
                Pill(text = option.label, selected = option == voice) { onPick(option) }
            }
        }
    }
}

@Composable
private fun AppPicker(
    apps: List<AppEntry>,
    onPick: (AppEntry) -> Unit,
    onDismiss: () -> Unit,
) {
    var query by remember { mutableStateOf("") }
    val shown = remember(apps, query) {
        if (query.isBlank()) apps else apps.filter { it.label.contains(query.trim(), ignoreCase = true) }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(usePlatformDefaultWidth = false),
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(Oyi.Black)
                .statusBarsPadding()
                .padding(horizontal = Gutter),
        ) {
            Row(
                modifier = Modifier.padding(top = 12.dp, bottom = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Add a distraction",
                    style = MaterialTheme.typography.headlineMedium,
                    color = Oyi.OffWhite,
                    modifier = Modifier.weight(1f),
                )
                IconButton(
                    icon = R.drawable.ic_close,
                    contentDescription = "Close",
                    onClick = onDismiss,
                    tint = Oyi.OffWhite,
                    background = Oyi.Raised,
                )
            }
            Spacer(Modifier.height(12.dp))
            OyiTextField(
                value = query,
                onValueChange = { query = it.take(40) },
                placeholder = "Search apps",
                icon = R.drawable.ic_apps,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.height(8.dp))
            if (apps.isEmpty()) {
                Row(Modifier.padding(vertical = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                    MorphingLoader(color = Oyi.Muted, size = 20.dp)
                    Spacer(Modifier.width(10.dp))
                    Text("Loading your apps", color = Oyi.Muted, fontSize = 14.sp)
                }
            }
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(bottom = 24.dp),
            ) {
                items(shown, key = { it.packageName }) { entry ->
                    ListRow(
                        title = entry.label,
                        leading = { AppIcon(entry.packageName, label = entry.label) },
                        onClick = { onPick(entry) },
                    ) {
                        Icon(painterResource(R.drawable.ic_add), contentDescription = "Add", tint = Oyi.Orange, modifier = Modifier.size(22.dp))
                    }
                }
            }
        }
    }
}

private val DAY_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("d MMM")

private fun formatDay(millis: Long): String =
    Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()).format(DAY_FORMAT)
