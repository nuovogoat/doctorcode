package com.nuovogoat.doctorcode.data

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

class ApiException(val statusCode: Int, message: String) : IOException(message)

/**
 * Thin client for the desktop bridge.
 *
 * The WebSocket carries events; prompts, STOP, and mode changes use REST so the
 * phone receives precise HTTP status codes (409 busy, 401 expired token, ...).
 * The bearer token travels in the Authorization header, never in a URL.
 */
class DoctorCodeApi(
    private val client: OkHttpClient = defaultClient(),
) {
    suspend fun health(baseUrl: String): Health =
        getJson(baseUrl, null, "api", "health")

    suspend fun pair(baseUrl: String, code: String, deviceName: String): PairResponse =
        postJson(baseUrl, null, PairRequest(code, deviceName), "api", "pair")

    suspend fun projects(baseUrl: String, token: String): ProjectsEnvelope =
        getJson(baseUrl, token, "api", "projects")

    suspend fun project(baseUrl: String, token: String, sessionId: String): ProjectEnvelope =
        getJson(baseUrl, token, "api", "projects", sessionId)

    suspend fun openProject(baseUrl: String, token: String, path: String): ProjectEnvelope =
        postJson(baseUrl, token, OpenProjectRequest(path), "api", "projects")

    suspend fun events(
        baseUrl: String,
        token: String,
        sessionId: String,
        limit: Int = 200,
    ): EventsEnvelope {
        val request = authorized(
            url(baseUrl, "api", "projects", sessionId, "events")
                .newBuilder()
                .addQueryParameter("limit", limit.toString())
                .build(),
            token,
        ).get().build()
        return DoctorCodeJson.decodeFromString(execute(request))
    }

    suspend fun prompt(
        baseUrl: String,
        token: String,
        sessionId: String,
        prompt: String,
        auto: Boolean? = null,
    ): PromptResponse = postJson(
        baseUrl,
        token,
        PromptRequest(prompt = prompt, auto = auto),
        "api",
        "projects",
        sessionId,
        "prompt",
    )

    suspend fun stop(baseUrl: String, token: String, sessionId: String): StopResponse {
        val request = authorized(
            url(baseUrl, "api", "projects", sessionId, "stop"),
            token,
        ).post(EMPTY_BODY).build()
        return DoctorCodeJson.decodeFromString(execute(request))
    }

    suspend fun setAuto(
        baseUrl: String,
        token: String,
        sessionId: String,
        enabled: Boolean,
    ): ProjectEnvelope = postJson(
        baseUrl,
        token,
        AutoRequest(enabled),
        "api",
        "projects",
        sessionId,
        "auto",
    )

    fun openStream(
        baseUrl: String,
        token: String,
        listener: WebSocketListener,
    ): WebSocket {
        val request = authorized(url(baseUrl, "ws"), token).build()
        return client.newWebSocket(request, listener)
    }

    fun close() {
        client.dispatcher.cancelAll()
        client.connectionPool.evictAll()
    }

    private suspend inline fun <reified T> getJson(
        baseUrl: String,
        token: String?,
        vararg segments: String,
    ): T {
        val httpUrl = url(baseUrl, *segments)
        val builder = if (token == null) {
            Request.Builder().url(httpUrl)
        } else {
            authorized(httpUrl, token)
        }
        return DoctorCodeJson.decodeFromString(execute(builder.get().build()))
    }

    private suspend inline fun <reified T, reified B> postJson(
        baseUrl: String,
        token: String?,
        body: B,
        vararg segments: String,
    ): T {
        val httpUrl = url(baseUrl, *segments)
        val builder = if (token == null) {
            Request.Builder().url(httpUrl)
        } else {
            authorized(httpUrl, token)
        }
        val payload = DoctorCodeJson.encodeToString(body).toRequestBody(JSON)
        return DoctorCodeJson.decodeFromString(execute(builder.post(payload).build()))
    }

    private fun authorized(url: HttpUrl, token: String): Request.Builder =
        Request.Builder().url(url).header("Authorization", "Bearer $token")

    private fun url(baseUrl: String, vararg segments: String): HttpUrl {
        val builder = baseUrl.toHttpUrl().newBuilder()
        segments.filter { it.isNotBlank() }.forEach { builder.addPathSegment(it) }
        return builder.build()
    }

    private suspend fun execute(request: Request): String =
        suspendCancellableCoroutine { continuation ->
            val call = client.newCall(request)
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (continuation.isActive) continuation.resumeWithException(e)
                }

                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        val body = it.body?.string().orEmpty()
                        if (!it.isSuccessful) {
                            if (continuation.isActive) {
                                continuation.resumeWithException(
                                    ApiException(it.code, errorText(body, it.code)),
                                )
                            }
                        } else if (continuation.isActive) {
                            continuation.resume(body)
                        }
                    }
                }
            })
        }

    private fun errorText(body: String, status: Int): String {
        if (body.isBlank()) return "HTTP $status"
        val parsed = runCatching {
            val root = DoctorCodeJson.parseToJsonElement(body).jsonObject
            root["detail"]?.jsonPrimitive?.contentOrNull
                ?: root["error"]?.jsonPrimitive?.contentOrNull
                ?: root["message"]?.jsonPrimitive?.contentOrNull
        }.getOrNull()
        return parsed?.takeIf { it.isNotBlank() } ?: body.take(300)
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
        val EMPTY_BODY = ByteArray(0).toRequestBody(null)

        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }
}
