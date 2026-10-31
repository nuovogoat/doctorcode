"""Kimi Code import: scanning, snapshots, project merge, and dedupe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from doctorcode.engine.context import SessionStore, StoredSession
from doctorcode.engine.events import ActionStatus, ActionType
from doctorcode.engine.kimi_import import (
    ConnectModuleMissing,
    KimiImportError,
    KimiImportService,
    map_snapshot_messages,
    snapshot_filename,
)
from doctorcode.engine.workspace import WorkspaceManager

BASE_TIME = 1_700_000_000_000


def _metadata() -> dict[str, Any]:
    return {
        "type": "metadata",
        "protocol_version": "1",
        "created_at": BASE_TIME,
        "time": BASE_TIME,
    }


def _llm_request() -> dict[str, Any]:
    return {
        "type": "llm.request",
        "time": BASE_TIME + 20,
        "turnStep": "0.1",
        "provider": "openai",
        "model": "kimi-for-coding",
        "modelAlias": "kimi-code/kimi-for-coding",
        "thinkingEffort": "max",
    }


def _user_message(text: str, message_id: str = "msg_user") -> dict[str, Any]:
    return {
        "type": "context.append_message",
        "time": BASE_TIME + 10,
        "message": {
            "id": message_id,
            "role": "user",
            "origin": {"kind": "user"},
            "content": text,
        },
    }


def _loop_event(event: dict[str, Any], time: int) -> dict[str, Any]:
    return {
        "type": "context.append_loop_event",
        "time": time,
        "event": event,
    }


def _step_events() -> list[dict[str, Any]]:
    return [
        _loop_event(
            {"type": "step.begin", "turnId": "0", "step": 1, "uuid": "step-1", "time": BASE_TIME + 30},
            BASE_TIME + 30,
        ),
        _loop_event(
            {
                "type": "content.part",
                "stepUuid": "step-1",
                "part": {"type": "think", "think": "thinking"},
            },
            BASE_TIME + 31,
        ),
        _loop_event(
            {
                "type": "content.part",
                "stepUuid": "step-1",
                "part": {"type": "text", "text": "Hi there"},
            },
            BASE_TIME + 32,
        ),
        _loop_event(
            {
                "type": "tool.call",
                "stepUuid": "step-1",
                "toolCallId": "call-1",
                "name": "Bash",
                "args": {"command": "ls"},
            },
            BASE_TIME + 33,
        ),
        _loop_event(
            {
                "type": "tool.result",
                "toolCallId": "call-1",
                "result": {"output": "file.txt", "isError": False, "durationMs": 12},
            },
            BASE_TIME + 34,
        ),
        _loop_event(
            {
                "type": "step.end",
                "uuid": "step-1",
                "usage": {
                    "inputOther": 100,
                    "output": 20,
                    "inputCacheRead": 300,
                    "inputCacheCreation": 0,
                },
                "finishReason": "tool_calls",
                "rawFinishReason": "tool_calls",
                "messageId": "llm-1",
            },
            BASE_TIME + 40,
        ),
    ]


def _turn_ended() -> dict[str, Any]:
    return {
        "type": "turn.ended",
        "time": BASE_TIME + 50,
        "turnId": "0",
        "reason": "completed",
        "durationMs": 500,
        "traceId": "trace-1",
    }


def write_kimi_session(
    root: Path,
    *,
    project: Path,
    title: str = "Test session",
    workspace_id: str = "wd_test",
    session_id: str = "session_test",
    updated_at: int = BASE_TIME + 100,
    with_messages: bool = True,
) -> Path:
    """Create a real-shaped ``.kimi-code/sessions`` tree below *root*."""
    session = root / ".kimi-code" / "sessions" / workspace_id / session_id
    (session / "agents" / "main").mkdir(parents=True, exist_ok=True)
    state = {
        "id": session_id,
        "version": 2,
        "cwd": str(project),
        "archived": False,
        "agents": {"main": {"type": "main"}},
        "title": title,
        "titleKind": "generated",
        "lastTurnReason": "completed",
        "createdAt": BASE_TIME,
        "updatedAt": updated_at,
        "isCustomTitle": False,
    }
    (session / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    events: list[dict[str, Any]] = [_metadata()]
    if with_messages:
        events += [
            _llm_request(),
            _user_message("Hello"),
            *_step_events(),
            _turn_ended(),
        ]
    wire = session / "agents" / "main" / "wire.jsonl"
    wire.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
        newline="\n",
    )
    return session


@pytest.fixture
def workspace(settings_store, app_data: Path) -> WorkspaceManager:
    return WorkspaceManager(app_data_dir=app_data, settings_store=settings_store)


@pytest.fixture
def service(workspace: WorkspaceManager, app_data: Path) -> KimiImportService:
    return KimiImportService(workspace=workspace, data_dir=app_data)


def _snapshot(service: KimiImportService, root: Path) -> dict[str, Any]:
    candidates = service.scan(root)
    assert len(candidates) == 1
    return candidates[0].snapshot


def test_can_handle_accepts_the_data_root_or_its_parent(
    tmp_path: Path, service: KimiImportService
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    write_kimi_session(tmp_path, project=project)

    assert service.can_handle(tmp_path) is True
    assert service.can_handle(tmp_path / ".kimi-code") is True
    assert service.can_handle(project) is False
    assert service.can_handle(tmp_path / "missing") is False


def test_scan_reports_preview_and_keeps_warnings(
    tmp_path: Path, project: Path, service: KimiImportService
) -> None:
    write_kimi_session(tmp_path, project=project, title="Fix the parser")

    candidates = service.scan(tmp_path)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.session_id == "kimi-code:wd_test:session_test"
    assert candidate.title == "Fix the parser"
    assert candidate.project_path == str(project.resolve())
    assert candidate.message_count == 3
    assert candidate.subagent_count == 0
    assert candidate.warning_count == 0
    assert candidate.warning_codes == ()
    assert candidate.importable is True
    assert candidate.project_dir == project.resolve()


def test_scan_without_a_kimi_tree_is_a_typed_error(
    tmp_path: Path, service: KimiImportService
) -> None:
    with pytest.raises(KimiImportError, match="sessions"):
        service.scan(tmp_path)


def test_mapping_keeps_main_history_and_drops_think_and_system(
    tmp_path: Path, project: Path, service: KimiImportService
) -> None:
    write_kimi_session(tmp_path, project=project)
    snapshot = _snapshot(service, tmp_path)
    snapshot["messages"].insert(
        0,
        {
            "id": "system-1",
            "agent_id": "main",
            "role": "system",
            "content": "ignore me",
        },
    )
    snapshot["messages"].insert(
        1,
        {
            "id": "empty-user",
            "agent_id": "main",
            "role": "user",
            "content": "   ",
        },
    )
    snapshot["messages"].insert(
        2,
        {
            "id": "empty-assistant",
            "agent_id": "main",
            "role": "assistant",
            "content": "",
            "tool_calls": None,
        },
    )

    messages = map_snapshot_messages(snapshot)

    assert [message.role for message in messages] == ["user", "assistant", "tool"]
    assert messages[0].content == "Hello"
    assert messages[1].content == "Hi there"
    assert messages[1].tool_calls == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "Bash", "arguments": '{"command": "ls"}'},
        }
    ]
    assert messages[2].content == "file.txt"
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].name == "Bash"


def test_snapshot_is_saved_verbatim_under_a_windows_safe_name(
    tmp_path: Path, project: Path, service: KimiImportService, app_data: Path
) -> None:
    write_kimi_session(tmp_path, project=project)
    snapshot = _snapshot(service, tmp_path)

    target = service.save_snapshot(snapshot)

    assert target == app_data / "imports" / "kimi" / "kimi-code_wd_test_session_test.json"
    assert json.loads(target.read_text(encoding="utf-8")) == snapshot
    assert target.read_text(encoding="utf-8") == json.dumps(
        snapshot, ensure_ascii=False, indent=2
    )


def test_save_rejects_a_foreign_envelope(
    tmp_path: Path, service: KimiImportService
) -> None:
    with pytest.raises(KimiImportError, match="Unsupported"):
        service.save_snapshot(
            {
                "format": "opencode.session",
                "schema_version": 1,
                "session": {"id": "x"},
                "messages": [],
            }
        )


def test_import_merges_into_the_project_tab_once(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    source = write_kimi_session(tmp_path, project=project, title="Imported work")
    before = (source / "agents" / "main" / "wire.jsonl").read_bytes()
    snapshot = _snapshot(service, tmp_path)

    result = service.import_snapshot(snapshot)

    assert result.session_id == "kimi-code:wd_test:session_test"
    assert result.title == "Imported work"
    assert result.project_path == project.resolve()
    assert result.imported_messages == 3
    assert result.already_imported is False
    assert result.snapshot_path.is_file()

    session = workspace.open_project(project)
    assert [message.role for message in session.messages] == ["user", "assistant", "tool"]
    assert session.imports == ["kimi-code:wd_test:session_test"]
    events = [
        event
        for event in session.feed.events()
        if event.action_type is ActionType.SESSION and "import_id" in event.metadata
    ]
    assert len(events) == 1
    assert events[0].status is ActionStatus.SUCCESS
    assert events[0].metadata["import_id"] == "kimi-code:wd_test:session_test"
    assert events[0].metadata["imported_messages"] == 3

    stored = workspace.session_store.load(project)
    assert stored is not None
    assert stored.imports == ["kimi-code:wd_test:session_test"]
    assert len(stored.messages) == 3
    assert (source / "agents" / "main" / "wire.jsonl").read_bytes() == before


def test_import_is_idempotent_for_the_same_project(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    write_kimi_session(tmp_path, project=project)
    snapshot = _snapshot(service, tmp_path)

    first = service.import_snapshot(snapshot)
    second = service.import_snapshot(snapshot)

    assert first.imported_messages == 3
    assert second.already_imported is True
    assert second.imported_messages == 0
    session = workspace.open_project(project)
    assert len(session.messages) == 3
    assert session.imports == ["kimi-code:wd_test:session_test"]


def test_missing_project_keeps_the_snapshot_and_accepts_an_override(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    missing = tmp_path / "gone"
    write_kimi_session(tmp_path, project=missing)
    candidate = service.scan(tmp_path)[0]
    # Migration works from and into any project: a missing source project
    # folder never blocks a session that has main-agent history.
    assert candidate.importable is True
    assert candidate.project_dir is None

    with pytest.raises(KimiImportError, match="does not exist"):
        service.import_snapshot(candidate.snapshot)

    snapshot_path = service.snapshot_path(candidate.snapshot)
    assert snapshot_path.is_file()

    result = service.import_snapshot(candidate.snapshot, project_path=project)

    assert result.project_path == project.resolve()
    assert result.imported_messages == 3
    assert workspace.open_project(project).imports == [candidate.session_id]


def test_import_into_any_project_overrides_the_session_project(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    """A session can migrate into ANY project, not only its original one."""
    write_kimi_session(tmp_path, project=project, title="Move me")
    other = tmp_path / "other_project"
    other.mkdir()
    candidate = service.scan(tmp_path)[0]
    assert candidate.importable is True
    assert candidate.project_dir == project.resolve()

    result = service.import_snapshot(candidate.snapshot, project_path=other)

    assert result.project_path == other.resolve()
    assert result.imported_messages == 3
    assert workspace.open_project(other).imports == [candidate.session_id]
    # The original project tab was never opened by the import.
    assert [session.project_path for session in workspace.list()] == [other.resolve()]


def test_service_reports_a_missing_connect_module(
    tmp_path: Path,
    workspace: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doctorcode.engine import kimi_import as kimi_module

    monkeypatch.setattr(kimi_module, "KimiMigrator", None)
    with pytest.raises(ConnectModuleMissing, match="doctor_code_ai_connect"):
        KimiImportService(workspace=workspace, data_dir=tmp_path / "data")


def test_snapshot_without_messages_is_stored_but_not_opened(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    write_kimi_session(tmp_path, project=project, with_messages=False)
    candidate = service.scan(tmp_path)[0]
    assert candidate.message_count == 0
    assert candidate.importable is False

    result = service.import_snapshot(candidate.snapshot)

    assert result.imported_messages == 0
    assert result.project_path is None
    assert result.snapshot_path.is_file()
    assert workspace.list() == []


def test_snapshot_never_contains_settings_or_api_keys(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    settings_store,
) -> None:
    write_kimi_session(tmp_path, project=project)
    snapshot = _snapshot(service, tmp_path)

    result = service.import_snapshot(snapshot)
    text = result.snapshot_path.read_text(encoding="utf-8")

    assert settings_store.settings.profile("writer").api_key == "test-key"
    assert "test-key" not in text
    assert "profiles" not in text
    assert "api_key" not in text


def test_import_records_survive_a_restart(
    tmp_path: Path,
    project: Path,
    service: KimiImportService,
    workspace: WorkspaceManager,
) -> None:
    write_kimi_session(tmp_path, project=project)
    service.import_snapshot(_snapshot(service, tmp_path))
    workspace.close_all()

    reopened = workspace.open_project(project)

    assert reopened.imports == ["kimi-code:wd_test:session_test"]
    assert len(reopened.messages) == 3


def test_old_session_files_without_imports_still_load(
    tmp_path: Path, app_data: Path, project: Path
) -> None:
    store = SessionStore(app_data)
    path = store._path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": "old-session",
                "project_path": str(project),
                "project_name": "project",
                "messages": [{"role": "user", "content": "old"}],
            }
        ),
        encoding="utf-8",
    )

    restored = store.load(project)

    assert isinstance(restored, StoredSession)
    assert restored.imports == []
    assert restored.messages[0].content == "old"


def test_snapshot_filename_is_safe_and_bounded() -> None:
    assert snapshot_filename("kimi-code:wd:session") == "kimi-code_wd_session.json"
    assert snapshot_filename('bad<>:"/\\|?*name') == "bad_________name.json"
    long = "x" * 400
    name = snapshot_filename(long)
    assert len(name) <= 184
    assert name.endswith(".json")
    assert snapshot_filename(".") == "session.json"
