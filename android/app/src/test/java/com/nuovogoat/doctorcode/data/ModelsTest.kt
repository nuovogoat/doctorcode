package com.nuovogoat.doctorcode.data

import kotlinx.serialization.encodeToString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelsTest {
    @Test
    fun `pair request uses the desktop snake case contract`() {
        val payload = DoctorCodeJson.encodeToString(PairRequest("123456", "Pixel 9"))
        assertEquals("""{"code":"123456","device_name":"Pixel 9"}""", payload)
    }

    @Test
    fun `hello frame is parsed without losing the pairing flag`() {
        val message = parseStreamMessage(
            """{"type":"hello","app":"DoctorCode","version":"0.1.0","pairing_required":true}""",
        )
        assertTrue(message is StreamMessage.Hello)
        message as StreamMessage.Hello
        assertEquals("DoctorCode", message.app)
        assertEquals("0.1.0", message.version)
        assertTrue(message.pairingRequired)
    }

    @Test
    fun `snapshot frame carries projects and their events`() {
        val message = parseStreamMessage(
            """
            {
              "type": "snapshot",
              "projects": [
                {
                  "session_id": "s-1",
                  "project_name": "demo",
                  "project_path": "C:\\Projects\\demo",
                  "model": "test-model",
                  "auto_mode": true,
                  "busy": false,
                  "event_count": 1,
                  "writer_configured": true,
                  "reviewer_configured": false
                }
              ],
              "events": {
                "s-1": [
                  {
                    "event_id": "e-1",
                    "timestamp": "2026-10-28T10:00:00Z",
                    "session_id": "s-1",
                    "action_type": "run_command",
                    "status": "SUCCESS",
                    "human_summary": "Tests passed",
                    "raw_payload": "pytest",
                    "metadata": {"exit_code": 0},
                    "duration_ms": 1200
                  }
                ]
              },
              "pairing_required": false
            }
            """.trimIndent(),
        )
        assertTrue(message is StreamMessage.Snapshot)
        message as StreamMessage.Snapshot
        assertEquals(1, message.projects.size)
        assertEquals("demo", message.projects.first().projectName)
        assertTrue(message.projects.first().autoMode)
        assertFalse(message.pairingRequired)
        val event = message.events.getValue("s-1").single()
        assertEquals("run_command", event.actionType)
        assertEquals("SUCCESS", event.status)
        assertEquals(1200.0, event.durationMs ?: 0.0, 0.01)
        assertEquals("0", event.metadata.getValue("exit_code").toString())
    }

    @Test
    fun `event frame can be applied incrementally`() {
        val message = parseStreamMessage(
            """
            {
              "type": "event",
              "event": {
                "event_id": "e-2",
                "timestamp": "2026-10-28T10:00:01Z",
                "session_id": "s-1",
                "action_type": "llm_call",
                "status": "IN_PROGRESS",
                "human_summary": "Writing",
                "raw_payload": "",
                "metadata": {}
              }
            }
            """.trimIndent(),
        )
        assertTrue(message is StreamMessage.Event)
        val event = (message as StreamMessage.Event).event
        assertEquals("IN_PROGRESS", event.status)
        assertNull(event.durationMs)
        assertTrue(event.metadata.isEmpty())
    }

    @Test
    fun `acknowledgements expose stop and prompt decisions`() {
        val stop = parseStreamMessage("""{"type":"ack","action":"stop","stopped":true}""")
        assertTrue(stop is StreamMessage.Ack)
        assertEquals("stop", (stop as StreamMessage.Ack).action)
        assertEquals(true, stop.stopped)

        val prompt = parseStreamMessage("""{"type":"ack","action":"prompt","accepted":true}""")
        assertEquals(true, (prompt as StreamMessage.Ack).accepted)
    }

    @Test
    fun `error and pong frames keep their type`() {
        val error = parseStreamMessage("""{"type":"error","code":"BUSY","message":"Session is busy"}""")
        assertTrue(error is StreamMessage.Error)
        assertEquals("BUSY", (error as StreamMessage.Error).code)

        assertEquals(StreamMessage.Pong, parseStreamMessage("""{"type":"pong"}"""))
    }

    @Test
    fun `unknown frames are ignored rather than crashing the stream`() {
        assertEquals(
            StreamMessage.Unknown("future"),
            parseStreamMessage("""{"type":"future","payload":1}"""),
        )
        assertEquals(StreamMessage.Unknown(""), parseStreamMessage("{}"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `event frame without an event payload is rejected`() {
        parseStreamMessage("""{"type":"event"}""")
    }

    @Test(expected = IllegalArgumentException::class)
    fun `malformed json is rejected`() {
        parseStreamMessage("{not json")
    }
}
