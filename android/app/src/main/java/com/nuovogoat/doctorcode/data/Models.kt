package com.nuovogoat.doctorcode.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/** Shared JSON configuration for the desktop HTTP and WebSocket contracts. */
val DoctorCodeJson = Json {
    ignoreUnknownKeys = true
    isLenient = true
    explicitNulls = false
}

@Serializable
data class Health(
    val status: String = "",
    val app: String = "",
    val version: String = "",
    @SerialName("pairing_required") val pairingRequired: Boolean = true,
    val projects: Int = 0,
)

@Serializable
data class PairRequest(
    val code: String,
    @SerialName("device_name") val deviceName: String,
)

@Serializable
data class OpenProjectRequest(val path: String)

@Serializable
data class PairResponse(
    val ok: Boolean = false,
    val token: String = "",
    @SerialName("pairing_required") val pairingRequired: Boolean = true,
    val server: String = "",
    val version: String = "",
)

@Serializable
data class ProjectState(
    @SerialName("session_id") val sessionId: String,
    @SerialName("project_name") val projectName: String = "",
    @SerialName("project_path") val projectPath: String = "",
    val model: String = "",
    @SerialName("auto_mode") val autoMode: Boolean = false,
    val busy: Boolean = false,
    @SerialName("event_count") val eventCount: Int = 0,
    @SerialName("writer_configured") val writerConfigured: Boolean = false,
    @SerialName("reviewer_configured") val reviewerConfigured: Boolean = false,
)

@Serializable
data class ProjectEnvelope(val project: ProjectState)

@Serializable
data class ProjectsEnvelope(
    val projects: List<ProjectState> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class ActionEvent(
    @SerialName("event_id") val eventId: String = "",
    val timestamp: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("action_type") val actionType: String = "",
    val status: String = "",
    @SerialName("human_summary") val humanSummary: String = "",
    @SerialName("raw_payload") val rawPayload: String = "",
    val metadata: JsonObject = JsonObject(emptyMap()),
    @SerialName("duration_ms") val durationMs: Double? = null,
)

@Serializable
data class EventsEnvelope(
    @SerialName("session_id") val sessionId: String = "",
    val events: List<ActionEvent> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class PromptRequest(
    val prompt: String,
    val auto: Boolean? = null,
    val wait: Boolean = false,
)

@Serializable
data class AutoRequest(val enabled: Boolean)

@Serializable
data class PromptResponse(
    val accepted: Boolean = false,
    val completed: Boolean = false,
    val project: ProjectState? = null,
)

@Serializable
data class StopResponse(
    val stopped: Boolean = false,
    val project: ProjectState? = null,
)

@Serializable
private data class SnapshotEnvelope(
    val projects: List<ProjectState> = emptyList(),
    val events: Map<String, List<ActionEvent>> = emptyMap(),
    @SerialName("pairing_required") val pairingRequired: Boolean = true,
)

/** Parsed WebSocket envelope from ``/ws``. */
sealed interface StreamMessage {
    data class Hello(
        val app: String,
        val version: String,
        val pairingRequired: Boolean,
    ) : StreamMessage

    data class Snapshot(
        val projects: List<ProjectState>,
        val events: Map<String, List<ActionEvent>>,
        val pairingRequired: Boolean,
    ) : StreamMessage

    data class Event(val event: ActionEvent) : StreamMessage

    data class Ack(
        val action: String,
        val sessionId: String?,
        val stopped: Boolean?,
        val accepted: Boolean?,
    ) : StreamMessage

    data class Error(val code: String, val message: String) : StreamMessage

    data object Pong : StreamMessage

    data class Unknown(val type: String) : StreamMessage
}

/**
 * Parse one text frame without trusting it.  Malformed frames throw; callers
 * treat that as a dropped frame rather than a broken connection.
 */
fun parseStreamMessage(text: String): StreamMessage {
    val root = DoctorCodeJson.parseToJsonElement(text).jsonObject
    return when (val type = root["type"]?.jsonPrimitive?.contentOrNull.orEmpty()) {
        "hello" -> StreamMessage.Hello(
            app = root["app"]?.jsonPrimitive?.contentOrNull ?: "DoctorCode",
            version = root["version"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            pairingRequired = root["pairing_required"]?.jsonPrimitive?.booleanOrNull ?: true,
        )

        "snapshot" -> {
            val snapshot = DoctorCodeJson.decodeFromJsonElement<SnapshotEnvelope>(root)
            StreamMessage.Snapshot(
                projects = snapshot.projects,
                events = snapshot.events,
                pairingRequired = snapshot.pairingRequired,
            )
        }

        "event" -> {
            val element = root["event"]
                ?: throw IllegalArgumentException("event frame has no event payload")
            StreamMessage.Event(DoctorCodeJson.decodeFromJsonElement<ActionEvent>(element))
        }

        "ack" -> StreamMessage.Ack(
            action = root["action"]?.jsonPrimitive?.contentOrNull.orEmpty(),
            sessionId = root["session_id"]?.jsonPrimitive?.contentOrNull,
            stopped = root["stopped"]?.jsonPrimitive?.booleanOrNull,
            accepted = root["accepted"]?.jsonPrimitive?.booleanOrNull,
        )

        "error" -> StreamMessage.Error(
            code = root["code"]?.jsonPrimitive?.contentOrNull ?: "UNKNOWN",
            message = root["message"]?.jsonPrimitive?.contentOrNull.orEmpty(),
        )

        "pong" -> StreamMessage.Pong
        else -> StreamMessage.Unknown(type)
    }
}
