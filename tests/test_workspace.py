"""Workspace tests: every project tab owns its own history and settings."""

from __future__ import annotations

from pathlib import Path

from doctorcode.engine.context import MessageRecord
from doctorcode.engine.events import ActionType
from doctorcode.engine.workspace import WorkspaceManager


def test_project_sessions_are_isolated(
    tmp_path: Path,
    app_data: Path,
    settings_store,
) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    manager = WorkspaceManager(
        app_data_dir=app_data,
        settings_store=settings_store,
    )

    first = manager.open_project(first_project)
    second = manager.open_project(second_project)
    first.messages.append(MessageRecord(role="user", content="first-only"))
    first.set_auto_mode(True)

    assert first.session_id != second.session_id
    assert second.messages == []
    assert second.auto_mode is False
    assert second.project_name == "second"
    assert [event.action_type for event in second.feed.events()] == [ActionType.SESSION]
    assert all("first-only" not in event.human_summary for event in second.feed.events())

    manager.close_all()


def test_reopening_a_project_restores_its_own_state(
    tmp_path: Path,
    app_data: Path,
    settings_store,
) -> None:
    project = tmp_path / "restored"
    project.mkdir()
    manager = WorkspaceManager(app_data_dir=app_data, settings_store=settings_store)
    original = manager.open_project(project)
    original.messages.append(MessageRecord(role="user", content="remember me"))
    original.set_auto_mode(True)
    original.model = "chosen-model"
    original.save()
    manager.close_all()

    restarted = WorkspaceManager(app_data_dir=app_data, settings_store=settings_store)
    restored = restarted.open_project(project)

    assert restored.session_id == original.session_id
    assert [message.content for message in restored.messages] == ["remember me"]
    assert restored.auto_mode is True
    assert restored.model == "chosen-model"
    restarted.close_all()


def test_opening_same_folder_returns_one_tab(
    tmp_path: Path,
    app_data: Path,
    settings_store,
) -> None:
    project = tmp_path / "same"
    project.mkdir()
    manager = WorkspaceManager(app_data_dir=app_data, settings_store=settings_store)

    first = manager.open_project(project)
    again = manager.open_project(project / ".")

    assert first is again
    assert len(manager.list()) == 1
    manager.close_all()


def test_workspace_fanout_sees_current_and_future_tabs(
    tmp_path: Path,
    app_data: Path,
    settings_store,
) -> None:
    manager = WorkspaceManager(app_data_dir=app_data, settings_store=settings_store)
    seen: list[str] = []
    manager.subscribe_all(lambda event: seen.append(event.session_id))

    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = manager.open_project(tmp_path / "one")
    second = manager.open_project(tmp_path / "two")

    assert first.session_id in seen
    assert second.session_id in seen
    manager.close_all()
