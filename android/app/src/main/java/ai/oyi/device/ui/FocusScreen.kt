package ai.oyi.device.ui

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
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

@Composable
fun FocusScreen(viewModel: FocusViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val live by viewModel.live.collectAsStateWithLifecycle()
    val accessibilityReady by viewModel.accessibilityReady.collectAsStateWithLifecycle()
    val notice by viewModel.notice.collectAsStateWithLifecycle()
    var showPicker by remember { mutableStateOf(false) }
    val context = LocalContext.current

    LaunchedEffect(notice) {
        if (notice.isNotEmpty()) {
            delay(3500)
            viewModel.clearNotice()
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Spacer(Modifier.height(6.dp))
            Text(
                text = if (state.session != null) "In\nFocus" else "Focus",
                fontSize = 36.sp,
                lineHeight = 40.sp,
                color = Oyi.OffWhite,
                fontWeight = FontWeight.Light,
            )
            if (notice.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Text(notice, color = Oyi.Orange, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            }
        }

        if (!accessibilityReady) {
            item {
                GlassCard(brush = Oyi.OrangeGlass, rim = Oyi.WarmRim) {
                    Text(
                        "Blocking is asleep",
                        color = Oyi.Ink,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.Bold,
                    )
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "OYI needs Control access to see which app is open and cover the ones you block.",
                        color = Oyi.InkSoft,
                        fontSize = 13.sp,
                    )
                    Spacer(Modifier.height(12.dp))
                    Pill(text = "Turn on Control access", selected = true, onDark = false) {
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }
                }
            }
        }

        item {
            val session = state.session
            if (session == null) {
                StartBlockCard(onStart = viewModel::startSession)
            } else {
                ActiveBlockCard(session = session, live = live, onDone = viewModel::finishSession)
            }
        }

        item {
            DistractionsCard(
                state = state,
                live = live,
                labelOf = viewModel::label,
                onAdd = {
                    viewModel.loadApps()
                    showPicker = true
                },
                onSetLimit = viewModel::setDistraction,
                onRemove = viewModel::removeDistraction,
            )
        }

        item {
            SitesCard(
                sites = state.sites,
                canLoosen = live.lock.canLoosen,
                onAdd = viewModel::addSite,
                onRemove = viewModel::removeSite,
            )
        }

        item {
            LockCard(
                status = live.lock,
                now = live.now,
                onArm = viewModel::armLock,
                onDisarm = viewModel::disarmLock,
                onEmergency = viewModel::requestEmergencyExit,
                onCancelEmergency = viewModel::cancelEmergencyExit,
            )
        }

        item {
            VoiceCard(voice = state.voice, onPick = viewModel::setVoice)
            Spacer(Modifier.height(28.dp))
        }
    }

    if (showPicker) {
        val apps by viewModel.apps.collectAsStateWithLifecycle()
        val taken = state.distractions.map { it.packageName }.toSet()
        AppPicker(
            apps = apps.filter { it.packageName !in taken },
            onPick = { entry ->
                viewModel.setDistraction(entry.packageName, LIMIT_FOCUS_ONLY)
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

    GlassCard {
        Eyebrow("Next block")
        Spacer(Modifier.height(10.dp))
        BasicTextField(
            value = task,
            onValueChange = { task = it.take(80) },
            singleLine = true,
            textStyle = TextStyle(
                color = Oyi.OffWhite,
                fontSize = 22.sp,
                fontWeight = FontWeight.Light,
            ),
            cursorBrush = SolidColor(Oyi.Orange),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
            modifier = Modifier.fillMaxWidth(),
            decorationBox = { inner ->
                Box {
                    if (task.isEmpty()) {
                        Text(
                            "What will you work on?",
                            color = Oyi.Faint,
                            fontSize = 22.sp,
                            fontWeight = FontWeight.Light,
                        )
                    }
                    inner()
                }
            },
        )
        Spacer(Modifier.height(14.dp))
        Box(Modifier.fillMaxWidth().height(1.dp).background(Oyi.Hairline))
        Spacer(Modifier.height(14.dp))
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SESSION_LENGTHS.forEach { length ->
                Pill(text = "$length min", selected = minutes == length) { minutes = length }
            }
        }
        Spacer(Modifier.height(18.dp))
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .clip(Oyi.Pill)
                .background(Oyi.OrangeGlass)
                .clickable {
                    focus.clearFocus()
                    onStart(task, minutes)
                    task = ""
                }
                .padding(vertical = 15.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text("Begin", color = Oyi.Ink, fontSize = 15.sp, fontWeight = FontWeight.Bold)
        }
        Spacer(Modifier.height(10.dp))
        Text(
            "Drift into a distraction and OYI nudges you, twice. After that it locks them until the block ends.",
            color = Oyi.Faint,
            fontSize = 12.sp,
            lineHeight = 17.sp,
        )
    }
}

@Composable
private fun ActiveBlockCard(session: FocusSession, live: FocusLive, onDone: () -> Unit) {
    val total = (session.endsAt - session.startedAt).coerceAtLeast(1L)
    val remaining = (session.endsAt - live.now).coerceIn(0L, total)
    val progress = 1f - remaining.toFloat() / total.toFloat()
    val (status, statusColor) = when (live.drift.stage) {
        DriftState.STAGE_LOCKDOWN -> "Locked down" to Oyi.Orange
        DriftState.STAGE_FIRM -> "Drifting. Last warning." to Oyi.Orange
        DriftState.STAGE_GENTLE -> "Drifting" to Oyi.Yellow
        else -> "On track" to Oyi.Cream
    }

    GlassCard {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.height(8.dp))
            FocusRing(progress = progress, modifier = Modifier.size(208.dp)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = formatClock(remaining),
                        color = Oyi.OffWhite,
                        fontSize = 44.sp,
                        fontWeight = FontWeight.ExtraLight,
                        fontFamily = FontFamily.Monospace,
                    )
                    Text("left", color = Oyi.Muted, fontSize = 12.sp)
                }
            }
            Spacer(Modifier.height(18.dp))
            Text(
                text = session.task.ifBlank { "Focus" },
                color = Oyi.OffWhite,
                fontSize = 20.sp,
                fontWeight = FontWeight.Medium,
                textAlign = TextAlign.Center,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(7.dp).clip(CircleShape).background(statusColor))
                Spacer(Modifier.width(8.dp))
                Text(status, color = statusColor, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            }
            Spacer(Modifier.height(20.dp))
            Pill(text = "I finished this", selected = true, onClick = onDone)
            Spacer(Modifier.height(4.dp))
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

    GlassCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Eyebrow("Distractions")
                Spacer(Modifier.height(4.dp))
                Text(
                    text = if (state.distractions.isEmpty()) "Nothing yet" else "${state.distractions.size} apps",
                    color = Oyi.OffWhite,
                    fontSize = 19.sp,
                    fontWeight = FontWeight.Medium,
                )
            }
            Pill(text = "+ Add app", selected = true, onClick = onAdd)
        }

        if (state.distractions.isEmpty()) {
            Spacer(Modifier.height(10.dp))
            Text(
                "Pick the apps that pull you away. Give each a daily allowance, or block it outright.",
                color = Oyi.Faint,
                fontSize = 12.5.sp,
                lineHeight = 18.sp,
            )
        }

        state.distractions.forEach { item ->
            Spacer(Modifier.height(14.dp))
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
        item.dailyLimitMin < 0 -> "Blocked only when you drift in focus"
        else -> "${usedMs / 60_000L}m of ${item.dailyLimitMin}m used today"
    }
    Column {
        Row(
            modifier = Modifier.fillMaxWidth().clickable(onClick = onToggle),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            AppIcon(item.packageName)
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    label,
                    color = Oyi.OffWhite,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(detail, color = Oyi.Muted, fontSize = 12.sp, maxLines = 1)
            }
            Text(if (open) "Close" else "Edit", color = Oyi.Faint, fontSize = 12.sp)
        }
        if (open) {
            Spacer(Modifier.height(10.dp))
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
                Pill(text = "Remove", enabled = canLoosen, onClick = onRemove)
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

    GlassCard {
        Eyebrow("Blocked sites")
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            BasicTextField(
                value = draft,
                onValueChange = { draft = it.take(120) },
                singleLine = true,
                textStyle = TextStyle(color = Oyi.OffWhite, fontSize = 16.sp),
                cursorBrush = SolidColor(Oyi.Orange),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(onDone = { submit() }),
                modifier = Modifier.weight(1f),
                decorationBox = { inner ->
                    Box {
                        if (draft.isEmpty()) {
                            Text("youtube.com", color = Oyi.Faint, fontSize = 16.sp)
                        }
                        inner()
                    }
                },
            )
            Spacer(Modifier.width(10.dp))
            Pill(text = "Block", selected = draft.isNotBlank(), onClick = { submit() })
        }
        if (sites.isNotEmpty()) {
            Spacer(Modifier.height(12.dp))
            Box(Modifier.fillMaxWidth().height(1.dp).background(Oyi.Hairline))
        }
        sites.forEach { site ->
            Spacer(Modifier.height(12.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    site,
                    color = Oyi.OffWhite,
                    fontSize = 15.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "Remove",
                    color = if (canLoosen) Oyi.Muted else Oyi.Faint.copy(alpha = 0.5f),
                    fontSize = 12.sp,
                    modifier = Modifier.clickable { onRemove(site) }.padding(6.dp),
                )
            }
        }
        Spacer(Modifier.height(10.dp))
        Text(
            "Works in Chrome, Firefox, Brave, Edge, Opera, Samsung Internet and DuckDuckGo.",
            color = Oyi.Faint,
            fontSize = 12.sp,
            lineHeight = 17.sp,
        )
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

    GlassCard(
        brush = if (armed) Oyi.CreamGlass else Oyi.SmokedGlass,
        rim = if (armed) Oyi.WarmRim else Oyi.DarkRim,
    ) {
        val ink = if (armed) Oyi.Ink else Oyi.OffWhite
        val soft = if (armed) Oyi.InkSoft else Oyi.Muted
        Eyebrow("Monthly lock", color = soft)
        Spacer(Modifier.height(6.dp))
        val headline = when (status.phase) {
            LockPhase.OFF -> "Off"
            LockPhase.LOCKED -> "Locked"
            LockPhase.WINDOW_OPEN -> "Window open"
            LockPhase.EMERGENCY_WAIT -> "Exit requested"
            LockPhase.EMERGENCY_OPEN -> "Exit open"
        }
        Text(headline, color = ink, fontSize = 26.sp, fontWeight = FontWeight.Light)
        Spacer(Modifier.height(6.dp))
        val body = when (status.phase) {
            LockPhase.OFF ->
                "Turn it on and you can still add blocks, but removing or relaxing them only opens on the 1st of each month."
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
        Text(body, color = soft, fontSize = 13.sp, lineHeight = 19.sp)

        if (status.phase == LockPhase.LOCKED && !status.needsSync) {
            Spacer(Modifier.height(14.dp))
            MonthBar(now = now, opens = status.until)
        }

        Spacer(Modifier.height(16.dp))
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            when (status.phase) {
                LockPhase.OFF -> Pill(
                    text = if (confirmArm) "Tap again to lock" else "Turn on lock",
                    selected = true,
                ) {
                    if (confirmArm) {
                        confirmArm = false
                        onArm()
                    } else {
                        confirmArm = true
                    }
                }
                LockPhase.LOCKED -> Pill(
                    text = if (confirmExit) "Tap again: 24h wait" else "Emergency exit",
                    onDark = false,
                ) {
                    if (confirmExit) {
                        confirmExit = false
                        onEmergency()
                    } else {
                        confirmExit = true
                    }
                }
                LockPhase.WINDOW_OPEN, LockPhase.EMERGENCY_OPEN -> Pill(
                    text = "Turn off lock",
                    selected = true,
                    onDark = false,
                    onClick = onDisarm,
                )
                LockPhase.EMERGENCY_WAIT -> Pill(
                    text = "Cancel exit",
                    selected = true,
                    onDark = false,
                    onClick = onCancelEmergency,
                )
            }
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
            .height(4.dp)
            .clip(Oyi.Pill)
            .background(Color(0x1F0A0807)),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(fraction)
                .height(4.dp)
                .clip(Oyi.Pill)
                .background(Oyi.Ink),
        )
    }
}

@Composable
private fun VoiceCard(voice: Voice, onPick: (Voice) -> Unit) {
    GlassCard {
        Eyebrow("Voice")
        Spacer(Modifier.height(4.dp))
        Text(
            "How OYI talks when you drift",
            color = Oyi.OffWhite,
            fontSize = 15.sp,
            fontWeight = FontWeight.Medium,
        )
        Spacer(Modifier.height(12.dp))
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
                .padding(horizontal = 20.dp, vertical = 24.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Add a\ndistraction",
                    color = Oyi.OffWhite,
                    fontSize = 30.sp,
                    lineHeight = 34.sp,
                    fontWeight = FontWeight.Light,
                    modifier = Modifier.weight(1f),
                )
                Pill(text = "Close", onClick = onDismiss)
            }
            Spacer(Modifier.height(18.dp))
            BasicTextField(
                value = query,
                onValueChange = { query = it.take(40) },
                singleLine = true,
                textStyle = TextStyle(color = Oyi.OffWhite, fontSize = 17.sp),
                cursorBrush = SolidColor(Oyi.Orange),
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(Oyi.Pill)
                    .background(Color(0x14FFFFFF))
                    .padding(horizontal = 18.dp, vertical = 13.dp),
                decorationBox = { inner ->
                    Box {
                        if (query.isEmpty()) Text("Search apps", color = Oyi.Faint, fontSize = 17.sp)
                        inner()
                    }
                },
            )
            Spacer(Modifier.height(10.dp))
            if (apps.isEmpty()) {
                Text("Loading your apps…", color = Oyi.Muted, fontSize = 13.sp, modifier = Modifier.padding(8.dp))
            }
            LazyColumn(modifier = Modifier.fillMaxSize()) {
                items(shown, key = { it.packageName }) { entry ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { onPick(entry) }
                            .padding(vertical = 10.dp, horizontal = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AppIcon(entry.packageName, size = 40.dp)
                        Spacer(Modifier.width(14.dp))
                        Text(
                            entry.label,
                            color = Oyi.OffWhite,
                            fontSize = 16.sp,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f),
                        )
                        Text("Add", color = Oyi.Orange, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }
        }
    }
}

private val DAY_FORMAT: DateTimeFormatter = DateTimeFormatter.ofPattern("d MMM")

private fun formatDay(millis: Long): String =
    Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()).format(DAY_FORMAT)
