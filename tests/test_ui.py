"""Headless GUI helpers plus an optional hidden Tk smoke test."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import threading
import time
from pathlib import Path

import pytest

from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.i18n import PACKS
from doctorcode.launch import hide_console_enabled, windowed_python
from doctorcode.runtime import create_runtime
from doctorcode.tools.base import ApprovalRequest
from doctorcode.ui.approval import ApprovalBroker
from doctorcode.ui.feed_model import (
    event_human_row,
    key_value_rows,
    raw_text,
    status_style,
    warning_text,
)
from doctorcode.ui.modules_logic import module_rows, pip_argv
from doctorcode.ui.runner import AsyncRunner


class DummyTranslator:
    def t(self, key: str, **_params: object) -> str:
        return key


def _event(**overrides: object) -> ActionEvent:
    data: dict[str, object] = {
        "session_id": "session-1",
        "action_type": ActionType.TOOL_CALL,
        "status": ActionStatus.SUCCESS,
        "human_summary": "Wrote a file.",
        "raw_payload": '{"ok": true}',
        "metadata": {"path": "a.txt", "nested": {"n": 1}},
    }
    data.update(overrides)
    return ActionEvent.model_validate(data)


def test_feed_model_human_row_and_raw() -> None:
    event = _event()
    row = event_human_row(event)
    assert len(row) == 4
    assert row[1] == "SUCCESS"
    assert row[2] == "tool_call"
    assert row[3] == "Wrote a file."
    assert raw_text(event, DummyTranslator()) == '{"ok": true}'
    assert ("path", "a.txt") in key_value_rows(event)


def test_feed_warning_uses_status_as_authority() -> None:
    event = _event(
        status=ActionStatus.FAILURE,
        human_summary="All tests passed successfully.",
    )
    assert event.has_status_summary_conflict
    assert status_style(event) == "conflict"
    assert warning_text(event, DummyTranslator()) == "feed.warning"
    assert status_style(_event(status=ActionStatus.IN_PROGRESS)) == "progress"


def test_module_status_comes_from_metadata_only() -> None:
    def lookup(distribution: str) -> str:
        if distribution == "PySide6":
            return "6.9.0"
        raise importlib.metadata.PackageNotFoundError(distribution)

    rows = module_rows(lookup)
    by_key = {row.spec.key: row for row in rows}
    assert by_key["qt"].installed
    assert by_key["qt"].version == "6.9.0"
    assert not by_key["dnd"].installed
    assert by_key["dnd"].status_key == "modules.not_installed"


def test_pip_argv_actions() -> None:
    assert pip_argv("install", "PySide6", python="py") == [
        "py",
        "-m",
        "pip",
        "--disable-pip-version-check",
        "install",
        "PySide6",
    ]
    assert pip_argv("update", "PySide6", python="py")[-2:] == ["--upgrade", "PySide6"]
    assert pip_argv("remove", "PySide6", python="py")[-3:] == [
        "uninstall",
        "--yes",
        "PySide6",
    ]


def test_launcher_reads_hide_console_fail_safe(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"ui": {"hide_console": True}}),
        encoding="utf-8",
    )
    assert hide_console_enabled(settings) is True
    settings.write_text("{broken", encoding="utf-8")
    assert hide_console_enabled(settings) is False
    settings.write_text(json.dumps({"ui": "not-a-dict"}), encoding="utf-8")
    assert hide_console_enabled(settings) is False


def test_windowed_python_detection(tmp_path: Path) -> None:
    console = tmp_path / "python.exe"
    windowed = tmp_path / "pythonw.exe"
    console.write_text("", encoding="utf-8")
    windowed.write_text("", encoding="utf-8")
    assert windowed_python(str(console)) == str(windowed)
    assert windowed_python(str(windowed)) == str(windowed)
    assert windowed_python(str(tmp_path / "python3")) is None


def test_approval_broker_roundtrip() -> None:
    broker = ApprovalBroker()
    result: dict[str, bool] = {}

    def worker() -> None:
        request = ApprovalRequest(
            action_type=ActionType.RUN_COMMAND,
            title="Run tests",
            details={"command": "pytest"},
        )
        result["approved"] = broker.request(request)

    thread = threading.Thread(target=worker)
    thread.start()
    pending = None
    for _ in range(200):
        pending = broker.next_pending()
        if pending is not None:
            break
        time.sleep(0.01)
    assert pending is not None
    pending.resolve(True)
    thread.join(timeout=3.0)
    assert result["approved"] is True


def test_approval_broker_close_denies_waiters() -> None:
    broker = ApprovalBroker()
    result: dict[str, bool] = {}

    def worker() -> None:
        result["approved"] = broker.request(
            ApprovalRequest(
                action_type=ActionType.WRITE_FILE,
                title="Write",
                details={},
            )
        )

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(200):
        pending = broker.next_pending()
        if pending is not None:
            break
        time.sleep(0.01)
    broker.close()
    thread.join(timeout=3.0)
    assert result["approved"] is False


def test_async_runner_runs_and_closes() -> None:
    runner = AsyncRunner(logging.getLogger("doctorcode.ui.tests"))
    try:
        future = runner.submit(asyncio.sleep(0, result=42))
        assert future.result(timeout=5.0) == 42
    finally:
        runner.shutdown()
    assert runner.closed


def test_i18n_packs_have_identical_keys_and_flags() -> None:
    base = set(PACKS["en"])
    for language, messages in PACKS.items():
        assert set(messages) == base, language
        assert f"language.flag.{language}" in messages


def test_qt_guard_reports_missing_optional_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("doctorcode.ui.qt_app")
    from doctorcode.ui import qt_app

    if qt_app.qt_available():
        pytest.skip("PySide6 is installed")
    monkeypatch.setenv("DOCTORCODE_DATA_DIR", str(tmp_path))
    assert qt_app.main([]) == 1
    assert "PySide6" in capsys.readouterr().err


def test_tk_app_hidden_smoke_transitions_without_blank_tabs(tmp_path: Path) -> None:
    tk = pytest.importorskip("tkinter")
    try:
        probe = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    probe.withdraw()
    probe.destroy()

    from doctorcode.ui.tk_app import DoctorCodeApp

    project = tmp_path / "gui-project"
    project.mkdir()
    runtime = create_runtime(tmp_path / "data")
    app = None
    try:
        app = DoctorCodeApp(runtime)
        assert app.selected_tab() is None
        tab = app.open_project(project)
        assert tab is not None
        assert app.selected_tab() is tab
        assert tab.session.project_name == project.name
        assert tab.session.project_path == project.resolve()
        assert app.root.title().endswith(project.name)
        app.root.update()
        assert len(app._tabs) == 1
    finally:
        if app is not None:
            app.close(confirm=False)
        else:
            runtime.close()
    assert app is not None and app._closed
