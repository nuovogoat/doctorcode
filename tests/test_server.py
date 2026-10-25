"""HTTP and WebSocket contract tests for the Android bridge."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from doctorcode.engine.llm_client import ChatTurn
from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.server.api import create_app
from doctorcode.server.security import PairingManager
from doctorcode.settings import SettingsStore


class FinalClient:
    async def complete(self, messages, tools) -> ChatTurn:
        return ChatTurn(
            content="server final",
            tool_calls=[],
            raw_payload=json.dumps({"model": "fake"}),
            finish_reason="stop",
        )

    async def list_models(self) -> list[str]:
        return ["fake-model"]

    async def close(self) -> None:
        return None


def build_app(tmp_path: Path, settings_store: SettingsStore):
    def mutate(settings):
        settings.server.advertise_mdns = False

    settings_store.update(mutate)
    manager = WorkspaceManager(
        app_data_dir=tmp_path / "app",
        settings_store=settings_store,
        client_factory=lambda profile, timeout=120.0: FinalClient(),
    )
    project = tmp_path / "server-project"
    project.mkdir()
    session = manager.open_project(project)
    pairing = PairingManager(require_pairing=True, token_file=None)
    app = create_app(
        manager=manager,
        settings_store=settings_store,
        pairing=pairing,
        reviewer_factory=lambda profile, timeout=120.0: FinalClient(),
        port=8765,
    )
    return app, manager, pairing, session, project


def test_pairing_and_full_control_flow(tmp_path: Path, settings_store: SettingsStore) -> None:
    app, manager, pairing, session, _project = build_app(tmp_path, settings_store)
    second = tmp_path / "second-project"
    second.mkdir()

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["pairing_required"] is True

        assert client.get("/api/projects").status_code == 401
        bad_code = f"{(int(pairing.code) + 1) % 1_000_000:06d}"
        assert client.post("/api/pair", json={"code": bad_code}).status_code == 401

        paired = client.post(
            "/api/pair",
            json={"code": pairing.code, "device_name": "Test phone"},
        )
        assert paired.status_code == 200
        token = paired.json()["token"]
        assert token
        headers = {"Authorization": f"Bearer {token}"}

        projects = client.get("/api/projects", headers=headers)
        assert projects.status_code == 200
        assert projects.json()["count"] == 1
        assert projects.json()["projects"][0]["session_id"] == session.session_id

        settings_payload = client.get("/api/settings", headers=headers).json()
        assert "test-key" not in json.dumps(settings_payload)
        assert settings_payload["server"]["paired_devices"] == 1
        assert settings_payload["discovery"]["running"] is False

        initial_events = client.get(
            f"/api/projects/{session.session_id}/events?limit=20",
            headers=headers,
        ).json()["events"]
        assert initial_events[0]["action_type"] == "session"

        prompt = client.post(
            f"/api/projects/{session.session_id}/prompt",
            headers=headers,
            json={"prompt": "do server work", "auto": True, "wait": True},
        )
        assert prompt.status_code == 200
        body = prompt.json()
        assert body["result"]["status"] == "SUCCESS"
        assert body["result"]["final_text"] == "server final"
        assert body["project"]["auto_mode"] is True

        events = client.get(
            f"/api/projects/{session.session_id}/events?limit=100",
            headers=headers,
        ).json()["events"]
        assert any(event["action_type"] == "user_prompt" for event in events)
        assert any(event["action_type"] == "agent_message" for event in events)

        stopped = client.post(
            f"/api/projects/{session.session_id}/stop",
            headers=headers,
        )
        assert stopped.status_code == 200
        assert stopped.json()["stopped"] is False

        auto = client.post(
            f"/api/projects/{session.session_id}/auto",
            headers=headers,
            json={"enabled": False},
        )
        assert auto.status_code == 200
        assert auto.json()["project"]["auto_mode"] is False

        switched = client.post(
            f"/api/projects/{session.session_id}/model",
            headers=headers,
            json={"model": "handoff-model"},
        )
        assert switched.status_code == 200
        assert switched.json()["project"]["model"] == "handoff-model"
        assert switched.json()["handoff"]["from_model"] == "test-model"
        assert switched.json()["handoff"]["to_model"] == "handoff-model"

        opened = client.post(
            "/api/projects",
            headers=headers,
            json={"path": str(second)},
        )
        assert opened.status_code == 200
        assert client.get("/api/projects", headers=headers).json()["count"] == 2

        review = client.post(
            f"/api/projects/{session.session_id}/review",
            headers=headers,
            json={"material": "print(1)", "instructions": "Check it."},
        )
        assert review.status_code == 200
        assert review.json()["status"] == "SUCCESS"
        assert review.json()["text"] == "server final"
        assert review.json()["events"][-1]["action_type"] == "review"

        with client.websocket_connect(f"/ws?token={token}") as websocket:
            hello = websocket.receive_json()
            assert hello["type"] == "hello"
            snapshot = websocket.receive_json()
            assert snapshot["type"] == "snapshot"
            assert snapshot["projects"]
            assert session.session_id in snapshot["events"]

            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}

            session.set_auto_mode(True)
            forwarded = websocket.receive_json()
            assert forwarded["type"] == "event"
            assert forwarded["event"]["action_type"] == "session"

            websocket.send_json({"type": "nonsense"})
            error = websocket.receive_json()
            assert error["type"] == "error"
            assert error["code"] == "UNKNOWN_MESSAGE"

            websocket.send_text("{not-json")
            malformed = websocket.receive_json()
            assert malformed["code"] == "BAD_JSON"

        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/ws?token=wrong"
        ):
            pass

        # A background prompt scheduled over HTTP must be able to finish before exit.
        assert session.busy is False
        assert client.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401

    manager.close_all()


def test_websocket_prompt_and_stop_commands(
    tmp_path: Path,
    settings_store: SettingsStore,
) -> None:
    app, manager, pairing, session, _project = build_app(tmp_path, settings_store)
    token = pairing.pair(pairing.code, "Socket phone").token
    assert token

    with TestClient(app) as client, client.websocket_connect(f"/ws?token={token}") as websocket:
        assert websocket.receive_json()["type"] == "hello"
        assert websocket.receive_json()["type"] == "snapshot"

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session.session_id,
                "prompt": "socket work",
                "auto": True,
            }
        )
        ack = websocket.receive_json()
        assert ack["type"] == "ack"
        assert ack["action"] == "prompt"

        saw_agent_message = False
        for _ in range(20):
            message = websocket.receive_json()
            if (
                message["type"] == "event"
                and message["event"]["action_type"] == "agent_message"
            ):
                saw_agent_message = True
                break
        assert saw_agent_message is True

        for _ in range(100):
            if session.busy is False:
                break
            time.sleep(0.01)
        assert session.busy is False

        websocket.send_json({"type": "stop", "session_id": session.session_id})
        stopped = websocket.receive_json()
        assert stopped["type"] == "ack"
        assert stopped["action"] == "stop"
        assert stopped["stopped"] is False

    manager.close_all()


def test_unknown_project_is_404(tmp_path: Path, settings_store: SettingsStore) -> None:
    app, manager, pairing, _session, _project = build_app(tmp_path, settings_store)
    token = pairing.pair(pairing.code).token
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        assert client.get("/api/projects/nope", headers=headers).status_code == 404
        assert client.post("/api/projects/nope/stop", headers=headers).status_code == 404
        assert (
            client.post("/api/projects", headers=headers, json={"path": str(tmp_path / "missing")})
            .status_code
            == 404
        )

    manager.close_all()
