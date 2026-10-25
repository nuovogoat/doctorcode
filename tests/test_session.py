"""Session lifecycle tests: cancellation, persistence, and model handoff."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from doctorcode.engine.context import MessageRecord
from doctorcode.engine.events import ActionStatus, ActionType
from doctorcode.engine.llm_client import ChatTurn


class BlockingClient:
    async def complete(self, messages, tools) -> ChatTurn:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def list_models(self) -> list[str]:
        return []

    async def close(self) -> None:
        return None


class FinalClient:
    async def complete(self, messages, tools) -> ChatTurn:
        return ChatTurn(content="finished", tool_calls=[], raw_payload="{}")

    async def list_models(self) -> list[str]:
        return []

    async def close(self) -> None:
        return None


class ClientFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, profile, *, timeout: float = 120.0):
        self.calls += 1
        return BlockingClient() if self.calls == 1 else FinalClient()


def test_stop_is_idempotent(project: Path, make_session) -> None:
    async def scenario() -> None:
        session = make_session(project, client_factory=ClientFactory())

        assert session.stop() is False
        assert session.stop() is False

        task = asyncio.create_task(session.run_prompt("do the work"))
        for _ in range(400):
            if session.busy:
                break
            await asyncio.sleep(0.005)
        assert session.busy is True

        assert session.stop() is True
        assert session.stop() is False
        result = await asyncio.wait_for(task, timeout=10.0)

        assert result.status is ActionStatus.FAILURE
        assert result.error_code == "CANCELLED"
        assert session.busy is False
        assert all(event.status is not ActionStatus.IN_PROGRESS for event in session.feed.events())

        recovered = await session.run_prompt("run again")
        assert recovered.status is ActionStatus.SUCCESS
        assert recovered.final_text == "finished"
        assert session.busy is False

    asyncio.run(scenario())


def test_model_switch_writes_handoff(git_project: Path, make_session) -> None:
    (git_project / "tracked.txt").write_bytes(b"original\nplus change\n")
    (git_project / "new.txt").write_bytes(b"untracked payload\n")
    session = make_session(git_project)
    session.messages.append(MessageRecord(role="user", content="keep this context"))
    session.messages.append(MessageRecord(role="assistant", content="and this answer"))
    previous = session.model

    snapshot = session.switch_model("next-model/v2")

    handoff_events = [
        event for event in session.feed.events() if event.action_type is ActionType.HANDOFF
    ]
    assert len(handoff_events) == 1
    event = handoff_events[0]
    assert event.status is ActionStatus.SUCCESS
    assert event.metadata["from_model"] == previous
    assert event.metadata["to_model"] == "next-model/v2"

    stored = session.handoff_store.load(Path(str(event.metadata["snapshot_path"])))
    assert stored.from_model == previous
    assert stored.to_model == "next-model/v2"
    assert [message.content for message in stored.messages] == [
        "keep this context",
        "and this answer",
    ]
    assert stored.workspace.is_git_repo is True
    files = {item.path: item for item in stored.workspace.changed_files}
    assert files["new.txt"].content == "untracked payload\n"
    assert files["tracked.txt"].content == "original\nplus change\n"
    assert "plus change" in stored.workspace.diff
    assert session.model == "next-model/v2"
    assert snapshot.workspace.files_omitted == 0

    restored = session.session_store.load(git_project)
    assert restored is not None
    assert restored.model == "next-model/v2"


def test_model_switch_rejects_empty_or_identical_model(
    project: Path,
    make_session,
) -> None:
    session = make_session(project)

    with pytest.raises(ValueError, match="must not be empty"):
        session.switch_model("  ")
    with pytest.raises(ValueError, match="already active"):
        session.switch_model(session.model)
    assert not session.handoff_store.list_for_project(project)


def test_missing_endpoint_is_reported_without_crashing(project: Path, app_data: Path) -> None:
    from doctorcode.engine.context import HandoffStore, SessionStore
    from doctorcode.engine.session import AgentSession
    from doctorcode.settings import SettingsStore

    store = SettingsStore(app_data / "blank-settings.json")
    session = AgentSession(
        project_path=project,
        settings_store=store,
        session_store=SessionStore(app_data),
        handoff_store=HandoffStore(app_data),
    )

    result = asyncio.run(session.run_prompt("hello"))

    assert result.status is ActionStatus.FAILURE
    assert result.error_code == "SETTINGS_MISSING_BASE_URL"
    assert session.busy is False
    assert session.feed.latest(1)[0].status is ActionStatus.FAILURE


def test_empty_prompt_is_rejected(project: Path, make_session) -> None:
    session = make_session(project)

    with pytest.raises(ValueError, match="must not be empty"):
        asyncio.run(session.run_prompt("   "))
