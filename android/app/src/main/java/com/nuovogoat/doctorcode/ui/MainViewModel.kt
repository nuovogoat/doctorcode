package com.nuovogoat.doctorcode.ui

import android.app.Application
import androidx.annotation.StringRes
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.nuovogoat.doctorcode.R
import com.nuovogoat.doctorcode.data.ActionEvent
import com.nuovogoat.doctorcode.data.ApiException
import com.nuovogoat.doctorcode.data.DiscoveredServer
import com.nuovogoat.doctorcode.data.DoctorCodeApi
import com.nuovogoat.doctorcode.data.NetworkPolicy
import com.nuovogoat.doctorcode.data.ProjectState
import com.nuovogoat.doctorcode.data.ServerDiscovery
import com.nuovogoat.doctorcode.data.SessionStore
import com.nuovogoat.doctorcode.data.StreamMessage
import com.nuovogoat.doctorcode.data.parseStreamMessage
import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

data class UiState(
    val baseUrl: String = "",
    val connected: Boolean = false,
    val connecting: Boolean = false,
    val serverVersion: String = "",
    val pairingRequired: Boolean = true,
    val projects: List<ProjectState> = emptyList(),
    val selectedSessionId: String? = null,
    val events: List<ActionEvent> = emptyList(),
    val autoMode: Boolean = false,
    val busy: Boolean = false,
    val sending: Boolean = false,
    val discovering: Boolean = false,
    val discovered: List<DiscoveredServer> = emptyList(),
    val draft: String = "",
    val listening: Boolean = false,
    val showConnect: Boolean = true,
    val notice: String? = null,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val store = SessionStore(application)
    private val api = DoctorCodeApi()
    private val discovery = ServerDiscovery(application)

    private var token: String = store.token
    private var streamJob: Job? = null
    private var pollJob: Job? = null
    private var socket: WebSocket? = null

    private val _state = MutableStateFlow(
        UiState(baseUrl = store.baseUrl, showConnect = true),
    )
    val state: StateFlow<UiState> = _state.asStateFlow()

    init {
        if (store.baseUrl.isNotBlank() && token.isNotBlank()) {
            connect(store.baseUrl, null)
        } else {
            startDiscovery()
        }
    }

    fun connect(address: String, code: String? = null) {
        val raw = address.trim()
        if (raw.isBlank()) {
            notify(message(R.string.address_invalid))
            return
        }
        if (_state.value.connecting) return
        viewModelScope.launch {
            _state.update {
                it.copy(
                    connecting = true,
                    showConnect = true,
                    notice = null,
                    baseUrl = raw,
                )
            }
            try {
                val base = try {
                    NetworkPolicy.normalizeBaseUrl(raw)
                } catch (exc: IllegalArgumentException) {
                    throw IllegalArgumentException(message(R.string.address_invalid), exc)
                }
                if (!NetworkPolicy.isAllowed(base)) {
                    throw IllegalArgumentException(message(R.string.private_network_only))
                }
                val health = api.health(base)
                var candidate = if (store.baseUrl == base) token else ""
                if (health.pairingRequired) {
                    if (candidate.isNotBlank()) {
                        try {
                            api.projects(base, candidate)
                        } catch (exc: ApiException) {
                            if (exc.statusCode == 401) {
                                candidate = ""
                                token = ""
                                store.token = ""
                            } else {
                                throw exc
                            }
                        }
                    }
                    if (candidate.isBlank()) {
                        if (code.isNullOrBlank()) {
                            throw IllegalArgumentException(message(R.string.pairing_required))
                        }
                        val paired = api.pair(base, code.trim(), store.deviceName)
                        candidate = paired.token
                        if (candidate.isBlank()) {
                            throw IllegalStateException(message(R.string.error_generic))
                        }
                    }
                } else {
                    candidate = ""
                }
                token = candidate
                store.baseUrl = base
                store.token = candidate
                _state.update {
                    it.copy(
                        baseUrl = base,
                        connected = true,
                        connecting = false,
                        serverVersion = health.version,
                        pairingRequired = health.pairingRequired,
                        showConnect = false,
                        notice = message(R.string.connected),
                    )
                }
                stopDiscovery()
                startStream(base, candidate)
                refreshProjects()
            } catch (exc: Exception) {
                _state.update {
                    it.copy(
                        connecting = false,
                        connected = false,
                        showConnect = true,
                        notice = connectionMessage(exc),
                    )
                }
                startDiscovery()
            }
        }
    }

    fun disconnect() {
        stopStream()
        _state.update {
            it.copy(
                connected = false,
                connecting = false,
                busy = false,
                sending = false,
                projects = emptyList(),
                events = emptyList(),
                selectedSessionId = null,
                showConnect = true,
            )
        }
        startDiscovery()
    }

    fun forgetServer() {
        stopStream()
        store.clear()
        token = ""
        _state.value = UiState(showConnect = true)
        startDiscovery()
    }

    fun setShowConnect(show: Boolean) {
        _state.update { it.copy(showConnect = show) }
    }

    fun startDiscovery() {
        if (_state.value.discovering) return
        _state.update { it.copy(discovering = true) }
        discovery.start(
            onFound = { server ->
                viewModelScope.launch {
                    _state.update { current ->
                        val others = current.discovered.filterNot { it.address == server.address }
                        current.copy(discovered = (others + server).sortedBy { it.name })
                    }
                }
            },
            onLost = { name ->
                viewModelScope.launch {
                    _state.update { current ->
                        current.copy(discovered = current.discovered.filterNot { it.name == name })
                    }
                }
            },
            onError = { text ->
                viewModelScope.launch {
                    _state.update { it.copy(discovering = false) }
                    notify(text)
                }
            },
        )
    }

    private fun stopDiscovery() {
        discovery.stop()
        _state.update { it.copy(discovering = false) }
    }

    fun refreshProjects() {
        val current = _state.value
        if (!current.connected) return
        viewModelScope.launch {
            try {
                val envelope = api.projects(current.baseUrl, token)
                applyProjects(envelope.projects)
            } catch (exc: Exception) {
                handleError(exc, message(R.string.projects_failed))
            }
        }
    }

    fun selectProject(sessionId: String) {
        val project = _state.value.projects.firstOrNull { it.sessionId == sessionId } ?: return
        _state.update {
            it.copy(
                selectedSessionId = sessionId,
                autoMode = project.autoMode,
                busy = project.busy,
                events = emptyList(),
            )
        }
        loadEvents(sessionId)
        if (project.busy) pollUntilIdle(sessionId)
    }

    fun openProject(path: String) {
        val trimmed = path.trim()
        if (trimmed.isBlank()) return
        val current = _state.value
        if (!current.connected) return
        viewModelScope.launch {
            _state.update { it.copy(connecting = true) }
            try {
                val project = api.openProject(current.baseUrl, token, trimmed).project
                val merged = if (current.projects.any { it.sessionId == project.sessionId }) {
                    current.projects.map {
                        if (it.sessionId == project.sessionId) project else it
                    }
                } else {
                    current.projects + project
                }
                applyProjects(merged, selectSessionId = project.sessionId)
                notify(message(R.string.project_opened, project.projectName))
            } catch (exc: Exception) {
                handleError(exc, message(R.string.project_open_failed))
            } finally {
                _state.update { it.copy(connecting = false) }
            }
        }
    }

    fun sendPrompt() {
        val current = _state.value
        val sessionId = current.selectedSessionId ?: return
        val text = current.draft.trim()
        if (text.isBlank() || current.sending) return
        _state.update { it.copy(sending = true, notice = null) }
        viewModelScope.launch {
            try {
                val response = api.prompt(current.baseUrl, token, sessionId, text, null)
                response.project?.let(::updateProject)
                _state.update {
                    it.copy(
                        draft = "",
                        sending = false,
                        busy = true,
                    )
                }
                pollUntilIdle(sessionId)
            } catch (exc: Exception) {
                _state.update { it.copy(sending = false) }
                handleError(exc, message(R.string.prompt_failed))
            }
        }
    }

    fun stop() {
        val current = _state.value
        val sessionId = current.selectedSessionId ?: return
        viewModelScope.launch {
            try {
                val response = api.stop(current.baseUrl, token, sessionId)
                response.project?.let(::updateProject)
                pollJob?.cancel()
                _state.update { it.copy(busy = false, sending = false) }
                notify(message(R.string.stop_sent))
                refreshProjects()
            } catch (exc: Exception) {
                handleError(exc, message(R.string.stop_failed))
            }
        }
    }

    fun setAuto(enabled: Boolean) {
        val current = _state.value
        val sessionId = current.selectedSessionId ?: return
        _state.update { it.copy(autoMode = enabled) }
        viewModelScope.launch {
            try {
                val project = api.setAuto(current.baseUrl, token, sessionId, enabled).project
                updateProject(project)
            } catch (exc: Exception) {
                _state.update { it.copy(autoMode = !enabled) }
                handleError(exc, message(R.string.auto_failed))
            }
        }
    }

    fun updateDraft(value: String) {
        _state.update { it.copy(draft = value.take(MAX_PROMPT_CHARS)) }
    }

    fun appendDraft(value: String) {
        val text = value.trim()
        if (text.isBlank()) return
        _state.update {
            val separator = if (it.draft.isBlank() || it.draft.endsWith(" ")) "" else " "
            it.copy(draft = (it.draft + separator + text).take(MAX_PROMPT_CHARS))
        }
    }

    fun setListening(value: Boolean) {
        _state.update { it.copy(listening = value) }
    }

    fun notify(text: String) {
        if (text.isBlank()) return
        _state.update { it.copy(notice = text) }
    }

    fun consumeNotice() {
        _state.update { it.copy(notice = null) }
    }

    private fun startStream(baseUrl: String, bearer: String) {
        streamJob?.cancel()
        socket?.close(1000, "reconnect")
        socket = null
        streamJob = viewModelScope.launch {
            var backoff = 1_000L
            while (isActive) {
                if (!_state.value.connected) break
                val closed = CompletableDeferred<Unit>()
                val webSocket = api.openStream(
                    baseUrl,
                    bearer,
                    object : WebSocketListener() {
                        override fun onOpen(webSocket: WebSocket, response: Response) {
                            backoff = 1_000L
                        }

                        override fun onMessage(webSocket: WebSocket, text: String) {
                            runCatching { parseStreamMessage(text) }
                                .onSuccess(::handleStreamMessage)
                        }

                        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                            webSocket.close(1000, null)
                        }

                        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                            if (!closed.isCompleted) closed.complete(Unit)
                        }

                        override fun onFailure(
                            webSocket: WebSocket,
                            t: Throwable,
                            response: Response?,
                        ) {
                            if (!closed.isCompleted) closed.complete(Unit)
                        }
                    },
                )
                socket = webSocket
                closed.await()
                socket = null
                if (!isActive) break
                notify(message(R.string.reconnecting))
                delay(backoff)
                backoff = (backoff * 2).coerceAtMost(15_000L)
            }
        }
    }

    private fun stopStream() {
        streamJob?.cancel()
        streamJob = null
        socket?.close(1000, "disconnect")
        socket = null
        pollJob?.cancel()
        pollJob = null
    }

    private fun handleStreamMessage(message: StreamMessage) {
        when (message) {
            is StreamMessage.Hello -> {
                _state.update {
                    it.copy(
                        serverVersion = message.version.ifBlank { it.serverVersion },
                        pairingRequired = message.pairingRequired,
                    )
                }
            }

            is StreamMessage.Snapshot -> applySnapshot(message)
            is StreamMessage.Event -> applyEvent(message.event)
            is StreamMessage.Ack -> {
                if (message.action == "stop") {
                    _state.update { it.copy(busy = false, sending = false) }
                }
                refreshProjects()
            }

            is StreamMessage.Error -> notify(
                message.message.ifBlank { message.code },
            )

            StreamMessage.Pong -> Unit
            is StreamMessage.Unknown -> Unit
        }
    }

    private fun applySnapshot(snapshot: StreamMessage.Snapshot) {
        val current = _state.value
        val selected = current.selectedSessionId
            ?.takeIf { id -> snapshot.projects.any { it.sessionId == id } }
            ?: snapshot.projects.firstOrNull()?.sessionId
        val project = snapshot.projects.firstOrNull { it.sessionId == selected }
        val keepEvents = selected == current.selectedSessionId && current.events.isNotEmpty()
        _state.update {
            it.copy(
                connected = true,
                showConnect = false,
                projects = snapshot.projects,
                selectedSessionId = selected,
                events = if (keepEvents) current.events else snapshot.events[selected].orEmpty(),
                autoMode = project?.autoMode ?: false,
                busy = project?.busy ?: false,
                pairingRequired = snapshot.pairingRequired,
            )
        }
        if (selected != null) {
            if (!keepEvents) loadEvents(selected)
            if (project?.busy == true) pollUntilIdle(selected)
        }
    }

    private fun applyEvent(event: ActionEvent) {
        _state.update { current ->
            if (event.sessionId != current.selectedSessionId) return@update current
            val events = current.events.toMutableList()
            val index = events.indexOfFirst { it.eventId == event.eventId }
            val isNew = index < 0
            if (isNew) events.add(event) else events[index] = event
            val trimmed = if (events.size > MAX_EVENTS) events.takeLast(MAX_EVENTS) else events
            val projects = if (isNew) {
                current.projects.map {
                    if (it.sessionId == event.sessionId) {
                        it.copy(eventCount = it.eventCount + 1)
                    } else {
                        it
                    }
                }
            } else {
                current.projects
            }
            current.copy(events = trimmed, projects = projects)
        }
    }

    private fun applyProjects(
        projects: List<ProjectState>,
        selectSessionId: String? = null,
    ) {
        val current = _state.value
        val selected = when {
            selectSessionId != null && projects.any { it.sessionId == selectSessionId } ->
                selectSessionId

            current.selectedSessionId != null &&
                projects.any { it.sessionId == current.selectedSessionId } ->
                current.selectedSessionId

            else -> projects.firstOrNull()?.sessionId
        }
        val project = projects.firstOrNull { it.sessionId == selected }
        val changed = selected != current.selectedSessionId
        _state.update {
            it.copy(
                projects = projects,
                selectedSessionId = selected,
                autoMode = project?.autoMode ?: false,
                busy = project?.busy ?: false,
                events = if (changed || selected == null) emptyList() else it.events,
            )
        }
        if (selected != null && changed) loadEvents(selected)
        if (selected != null && project?.busy == true) pollUntilIdle(selected)
    }

    private fun loadEvents(sessionId: String) {
        val current = _state.value
        if (!current.connected) return
        viewModelScope.launch {
            try {
                val envelope = api.events(current.baseUrl, token, sessionId, MAX_EVENTS)
                if (_state.value.selectedSessionId == sessionId) {
                    _state.update { it.copy(events = envelope.events) }
                }
            } catch (exc: Exception) {
                handleError(exc, message(R.string.projects_failed))
            }
        }
    }

    private fun pollUntilIdle(sessionId: String) {
        pollJob?.cancel()
        pollJob = viewModelScope.launch {
            while (isActive) {
                delay(1_500L)
                val current = _state.value
                if (!current.connected) return@launch
                val envelope = try {
                    api.project(current.baseUrl, token, sessionId)
                } catch (exc: Exception) {
                    if (exc is ApiException && exc.statusCode == 401) {
                        handleError(exc, message(R.string.pairing_expired))
                    }
                    return@launch
                }
                updateProject(envelope.project)
                if (_state.value.selectedSessionId == sessionId) {
                    _state.update { it.copy(sending = false) }
                }
                if (!envelope.project.busy) return@launch
            }
        }
    }

    private fun updateProject(project: ProjectState) {
        _state.update { current ->
            val projects = if (current.projects.any { it.sessionId == project.sessionId }) {
                current.projects.map {
                    if (it.sessionId == project.sessionId) project else it
                }
            } else {
                current.projects + project
            }
            val selected = current.selectedSessionId == project.sessionId
            current.copy(
                projects = projects,
                autoMode = if (selected) project.autoMode else current.autoMode,
                busy = if (selected) project.busy else current.busy,
            )
        }
    }

    private fun handleError(exc: Throwable, fallback: String) {
        if (exc is ApiException && exc.statusCode == 401) {
            token = ""
            store.token = ""
            notify(message(R.string.pairing_expired))
            disconnect()
            return
        }
        notify(exc.message?.takeIf { it.isNotBlank() } ?: fallback)
    }

    private fun connectionMessage(exc: Throwable): String = when (exc) {
        is ApiException -> exc.message ?: message(R.string.error_generic)
        is IllegalArgumentException -> exc.message ?: message(R.string.address_invalid)
        is IOException -> message(R.string.connection_failed, exc.message ?: "")
        else -> message(R.string.connection_failed, exc.message ?: message(R.string.error_generic))
    }

    private fun message(@StringRes id: Int, vararg args: Any): String =
        getApplication<Application>().getString(id, *args)

    override fun onCleared() {
        stopStream()
        discovery.stop()
        api.close()
        super.onCleared()
    }

    private companion object {
        const val MAX_EVENTS = 500
        const val MAX_PROMPT_CHARS = 200_000
    }
}
