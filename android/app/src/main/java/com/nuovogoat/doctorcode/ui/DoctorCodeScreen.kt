@file:OptIn(androidx.compose.material3.ExperimentalMaterial3Api::class)

package com.nuovogoat.doctorcode.ui

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.nuovogoat.doctorcode.R
import com.nuovogoat.doctorcode.data.ActionEvent
import com.nuovogoat.doctorcode.data.DiscoveredServer
import com.nuovogoat.doctorcode.data.ProjectState
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@Composable
fun DoctorCodeRoot(viewModel: MainViewModel = viewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val context = LocalContext.current
    val voice = remember { VoiceController(context) }

    val micPermission = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            voice.start()
        } else {
            viewModel.setListening(false)
            viewModel.notify(context.getString(R.string.voice_permission))
        }
    }

    LaunchedEffect(voice) {
        voice.onResult = { text ->
            viewModel.appendDraft(text)
            viewModel.setListening(false)
        }
        voice.onError = { message ->
            viewModel.setListening(false)
            viewModel.notify(message)
        }
        voice.onListening = { listening ->
            viewModel.setListening(listening)
        }
    }
    DisposableEffect(voice) {
        onDispose { voice.destroy() }
    }
    LaunchedEffect(state.notice) {
        state.notice?.let {
            snackbarHostState.showSnackbar(it)
            viewModel.consumeNotice()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            val subtitle = when {
                !state.connected -> stringResource(R.string.not_connected)
                state.serverVersion.isNotBlank() ->
                    stringResource(R.string.server_version, state.serverVersion)

                else -> stringResource(R.string.connected)
            }
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
                title = {
                    Column {
                        Text(
                            text = stringResource(R.string.app_name),
                            fontWeight = FontWeight.SemiBold,
                        )
                        Text(
                            text = subtitle,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    if (state.connected) {
                        IconButton(onClick = viewModel::refreshProjects) {
                            Icon(
                                imageVector = Icons.Filled.Refresh,
                                contentDescription = stringResource(R.string.refresh),
                            )
                        }
                    }
                    IconButton(onClick = { viewModel.setShowConnect(true) }) {
                        Icon(
                            imageVector = Icons.Filled.Settings,
                            contentDescription = stringResource(R.string.settings),
                        )
                    }
                },
            )
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (state.connected) {
                WorkspaceScreen(
                    state = state,
                    viewModel = viewModel,
                    onMic = {
                        when {
                            state.listening -> voice.stop()
                            ContextCompat.checkSelfPermission(
                                context,
                                Manifest.permission.RECORD_AUDIO,
                            ) == PackageManager.PERMISSION_GRANTED -> voice.start()

                            else -> micPermission.launch(Manifest.permission.RECORD_AUDIO)
                        }
                    },
                )
            }
            if (!state.connected || state.showConnect) {
                ConnectScreen(
                    state = state,
                    onConnect = viewModel::connect,
                    onRefresh = viewModel::startDiscovery,
                    onClose = if (state.connected) {
                        { viewModel.setShowConnect(false) }
                    } else {
                        null
                    },
                    onDisconnect = {
                        viewModel.disconnect()
                        viewModel.notify(context.getString(R.string.disconnected))
                    },
                    onForget = viewModel::forgetServer,
                    modifier = Modifier
                        .fillMaxSize()
                        .background(
                            MaterialTheme.colorScheme.background.copy(
                                alpha = if (state.connected) 0.98f else 1f,
                            ),
                        ),
                )
            }
        }
    }
}

@Composable
private fun ConnectScreen(
    state: UiState,
    onConnect: (String, String?) -> Unit,
    onRefresh: () -> Unit,
    onClose: (() -> Unit)?,
    onDisconnect: () -> Unit,
    onForget: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var address by rememberSaveable { mutableStateOf(state.baseUrl) }
    var code by rememberSaveable { mutableStateOf("") }

    Column(
        modifier = modifier
            .verticalScroll(rememberScrollState())
            .imePadding()
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = if (state.connected) {
                        stringResource(R.string.settings)
                    } else {
                        stringResource(R.string.connect_title)
                    },
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = stringResource(R.string.app_tagline),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (onClose != null) {
                TextButton(onClick = onClose) {
                    Text(stringResource(R.string.close))
                }
            }
        }

        if (!state.connected) {
            Text(
                text = stringResource(R.string.connect_body),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = onDisconnect) {
                    Text(stringResource(R.string.disconnect))
                }
                TextButton(onClick = onForget) {
                    Text(stringResource(R.string.forget_server))
                }
            }
            Text(
                text = stringResource(R.string.forget_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        HorizontalDivider()
        Text(
            text = stringResource(R.string.discovered_title),
            style = MaterialTheme.typography.titleMedium,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = stringResource(R.string.scan_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onRefresh, enabled = !state.discovering) {
                Text(stringResource(R.string.refresh_discovery))
            }
        }
        if (state.discovering) {
            LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            Text(
                text = stringResource(R.string.discovering),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (state.discovered.isEmpty()) {
            Text(
                text = stringResource(R.string.discovery_empty),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                state.discovered.forEach { server ->
                    DiscoveredServerCard(
                        server = server,
                        onSelect = {
                            address = server.address
                            code = ""
                        },
                    )
                }
            }
        }

        HorizontalDivider()
        Text(
            text = stringResource(R.string.manual_address),
            style = MaterialTheme.typography.titleMedium,
        )
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            label = { Text(stringResource(R.string.manual_address)) },
            placeholder = { Text(stringResource(R.string.manual_address_hint)) },
            singleLine = true,
            enabled = !state.connecting,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = code,
            onValueChange = { entered -> code = entered.filter(Char::isDigit).take(6) },
            label = { Text(stringResource(R.string.pairing_code)) },
            placeholder = { Text(stringResource(R.string.pairing_code_hint)) },
            singleLine = true,
            enabled = !state.connecting,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        Text(
            text = stringResource(R.string.pairing_optional),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Button(
            onClick = { onConnect(address, code.ifBlank { null }) },
            enabled = !state.connecting && address.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (state.connecting) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = MaterialTheme.colorScheme.onPrimary,
                )
                Spacer(Modifier.width(8.dp))
            }
            Text(
                stringResource(
                    if (state.connecting) R.string.connecting else R.string.connect,
                ),
            )
        }
        if (state.baseUrl.isNotBlank()) {
            TextButton(
                onClick = onForget,
                enabled = !state.connecting,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(stringResource(R.string.forget_server))
            }
        }

        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
            ),
        ) {
            Column(
                modifier = Modifier.padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = stringResource(R.string.security_title),
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    text = stringResource(R.string.security_body),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = stringResource(R.string.local_only_note),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun DiscoveredServerCard(
    server: DiscoveredServer,
    onSelect: () -> Unit,
) {
    Card(
        onClick = onSelect,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f),
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = server.name,
                    style = MaterialTheme.typography.titleSmall,
                )
                Text(
                    text = server.address,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                if (server.version.isNotBlank()) {
                    Text(
                        text = stringResource(R.string.server_version, server.version),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Text(
                    text = stringResource(
                        if (server.pairingRequired) {
                            R.string.pairing_required
                        } else {
                            R.string.pairing_disabled
                        },
                    ),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (server.pairingRequired) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }
    }
}

@Composable
private fun WorkspaceScreen(
    state: UiState,
    viewModel: MainViewModel,
    onMic: () -> Unit,
) {
    val selected = state.projects.firstOrNull { it.sessionId == state.selectedSessionId }
    var showAddProject by rememberSaveable { mutableStateOf(false) }

    Column(modifier = Modifier.fillMaxSize()) {
        ProjectStrip(
            state = state,
            onSelect = viewModel::selectProject,
            onAdd = { showAddProject = true },
        )
        if (selected == null) {
            EmptyProjects(
                onAdd = { showAddProject = true },
                modifier = Modifier.weight(1f),
            )
        } else {
            ProjectHeader(project = selected, state = state)
            EventFeed(
                events = state.events,
                modifier = Modifier.weight(1f),
            )
            Composer(
                state = state,
                onSend = viewModel::sendPrompt,
                onStop = viewModel::stop,
                onAuto = viewModel::setAuto,
                onDraft = viewModel::updateDraft,
                onMic = onMic,
            )
        }
    }
    if (showAddProject) {
        AddProjectDialog(
            onDismiss = { showAddProject = false },
            onOpen = { path ->
                showAddProject = false
                viewModel.openProject(path)
            },
        )
    }
}

@Composable
private fun ProjectStrip(
    state: UiState,
    onSelect: (String) -> Unit,
    onAdd: () -> Unit,
) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items(items = state.projects, key = { it.sessionId }) { project ->
            FilterChip(
                selected = project.sessionId == state.selectedSessionId,
                onClick = { onSelect(project.sessionId) },
                label = {
                    Text(
                        text = project.projectName.ifBlank {
                            project.sessionId.take(8)
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                leadingIcon = if (project.busy) {
                    {
                        CircularProgressIndicator(
                            modifier = Modifier.size(14.dp),
                            strokeWidth = 2.dp,
                        )
                    }
                } else {
                    null
                },
            )
        }
        item {
            AssistChip(
                onClick = onAdd,
                label = { Text(stringResource(R.string.add_project)) },
                leadingIcon = {
                    Icon(
                        imageVector = Icons.Filled.Add,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp),
                    )
                },
            )
        }
    }
}

@Composable
private fun ProjectHeader(project: ProjectState, state: UiState) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.3f),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = project.projectPath,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = stringResource(
                        if (state.busy) R.string.busy else R.string.idle,
                    ),
                    style = MaterialTheme.typography.labelSmall,
                    color = if (state.busy) {
                        MaterialTheme.colorScheme.primary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = "${stringResource(R.string.model_label)}: " +
                        project.model.ifBlank { "—" },
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = stringResource(R.string.event_count, state.events.size),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (!project.writerConfigured) {
                Text(
                    text = stringResource(R.string.writer_missing),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
        }
    }
}

@Composable
private fun EventFeed(events: List<ActionEvent>, modifier: Modifier = Modifier) {
    if (events.isEmpty()) {
        Box(
            modifier = modifier.fillMaxWidth(),
            contentAlignment = Alignment.Center,
        ) {
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text(
                    text = stringResource(R.string.feed_title),
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    text = stringResource(R.string.no_events),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        return
    }

    val listState = rememberLazyListState()
    LaunchedEffect(events.size) {
        if (events.isNotEmpty()) listState.animateScrollToItem(events.lastIndex)
    }
    LazyColumn(
        state = listState,
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(
            items = events,
            key = { event -> event.eventId.ifBlank { event.hashCode().toString() } },
        ) { event ->
            EventCard(event)
        }
    }
}

@Composable
private fun EventCard(event: ActionEvent) {
    val color = statusColor(event.status)
    var expanded by rememberSaveable(event.eventId) { mutableStateOf(false) }

    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
        ),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Surface(
                    color = color.copy(alpha = 0.16f),
                    shape = RoundedCornerShape(6.dp),
                ) {
                    Text(
                        text = statusLabel(event.status),
                        style = MaterialTheme.typography.labelSmall,
                        color = color,
                        fontWeight = FontWeight.Bold,
                        modifier = Modifier.padding(horizontal = 7.dp, vertical = 3.dp),
                    )
                }
                Spacer(Modifier.width(8.dp))
                Text(
                    text = event.actionType.replace('_', ' ').uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = formatTime(event.timestamp),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                text = event.humanSummary,
                style = MaterialTheme.typography.bodyMedium,
            )
            if (event.durationMs != null) {
                Text(
                    text = "${event.durationMs.toInt()} ms",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (event.rawPayload.isNotBlank() || event.metadata.isNotEmpty()) {
                TextButton(
                    onClick = { expanded = !expanded },
                    contentPadding = PaddingValues(horizontal = 0.dp, vertical = 2.dp),
                ) {
                    Text(
                        stringResource(
                            if (expanded) R.string.hide_details else R.string.details,
                        ),
                    )
                }
                if (expanded) {
                    SelectionContainer {
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            if (event.metadata.isNotEmpty()) {
                                Text(
                                    text = stringResource(R.string.metadata),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Text(
                                    text = event.metadata.toString(),
                                    style = MaterialTheme.typography.bodySmall,
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 11.sp,
                                )
                            }
                            if (event.rawPayload.isNotBlank()) {
                                Text(
                                    text = stringResource(R.string.raw_payload),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Text(
                                    text = event.rawPayload,
                                    style = MaterialTheme.typography.bodySmall,
                                    fontFamily = FontFamily.Monospace,
                                    fontSize = 11.sp,
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
private fun statusLabel(status: String): String = when (status.uppercase()) {
    "SUCCESS" -> stringResource(R.string.status_success)
    "FAILURE" -> stringResource(R.string.status_failure)
    else -> stringResource(R.string.status_in_progress)
}

@Composable
private fun Composer(
    state: UiState,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onAuto: (Boolean) -> Unit,
    onDraft: (String) -> Unit,
    onMic: () -> Unit,
) {
    Surface(
        tonalElevation = 3.dp,
        shadowElevation = 8.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .imePadding()
                .padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Row(
                verticalAlignment = Alignment.Bottom,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                OutlinedTextField(
                    value = state.draft,
                    onValueChange = onDraft,
                    label = { Text(stringResource(R.string.prompt_label)) },
                    placeholder = { Text(stringResource(R.string.prompt_hint)) },
                    maxLines = 4,
                    modifier = Modifier.weight(1f),
                )
                FilledIconButton(
                    onClick = onSend,
                    enabled = state.draft.isNotBlank() && !state.sending && !state.busy,
                ) {
                    Icon(
                        imageVector = Icons.AutoMirrored.Filled.Send,
                        contentDescription = stringResource(R.string.send),
                    )
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(
                    onClick = onMic,
                    enabled = !state.sending,
                ) {
                    Text(
                        stringResource(
                            if (state.listening) R.string.listening else R.string.voice_input,
                        ),
                    )
                }
                Column(modifier = Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            text = stringResource(R.string.auto_mode),
                            style = MaterialTheme.typography.labelMedium,
                        )
                        Spacer(Modifier.width(6.dp))
                        Switch(
                            checked = state.autoMode,
                            onCheckedChange = onAuto,
                            enabled = !state.sending,
                        )
                    }
                    Text(
                        text = stringResource(R.string.auto_description),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                Spacer(Modifier.width(8.dp))
                Button(
                    onClick = onStop,
                    enabled = state.selectedSessionId != null,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = MaterialTheme.colorScheme.error,
                        contentColor = MaterialTheme.colorScheme.onError,
                    ),
                ) {
                    Text(
                        text = stringResource(R.string.stop),
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptyProjects(onAdd: () -> Unit, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                text = stringResource(R.string.no_projects),
                style = MaterialTheme.typography.titleMedium,
            )
            Text(
                text = stringResource(R.string.no_projects_hint),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(onClick = onAdd) {
                Text(stringResource(R.string.add_project))
            }
        }
    }
}

@Composable
private fun AddProjectDialog(
    onDismiss: () -> Unit,
    onOpen: (String) -> Unit,
) {
    var path by rememberSaveable { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.open_project_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    text = stringResource(R.string.no_projects_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                OutlinedTextField(
                    value = path,
                    onValueChange = { path = it },
                    label = { Text(stringResource(R.string.open_project_title)) },
                    placeholder = { Text(stringResource(R.string.project_path_hint)) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = { onOpen(path.trim()) },
                enabled = path.isNotBlank(),
            ) {
                Text(stringResource(R.string.open_project))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.cancel))
            }
        },
    )
}

private val timeFormatter: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")

private fun formatTime(value: String): String {
    if (value.isBlank()) return ""
    return runCatching {
        timeFormatter.format(Instant.parse(value).atZone(ZoneId.systemDefault()))
    }.getOrElse { value.takeLast(8) }
}
