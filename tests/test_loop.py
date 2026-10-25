"""ReAct loop tests with deterministic fake model clients."""

from __future__ import annotations

import asyncio
from pathlib import Path

from core_pack import CancellationToken

from doctorcode.engine.context import MessageRecord
from doctorcode.engine.events import ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.llm_client import ChatTurn, LLMError, ToolCall
from doctorcode.engine.loop import AgentLoop
from doctorcode.settings import AgentSettings, ConfirmationSettings
from doctorcode.tools import ToolRegistry
from doctorcode.tools.base import ToolContext


class ScriptedClient:
    def __init__(self, turns: list[ChatTurn | Exception]) -> None:
        self.turns = list(turns)
        self.requests: list[list[dict]] = []

    async def complete(self, messages, tools) -> ChatTurn:
        self.requests.append(list(messages))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    async def list_models(self) -> list[str]:
        return []

    async def close(self) -> None:
        return None


def make_loop(project: Path, *, auto_mode: bool = True, max_iterations: int = 5):
    feed = ActionFeed()
    context = ToolContext(
        project_path=project,
        token=CancellationToken(),
        auto_mode=auto_mode,
        confirmations=ConfirmationSettings(),
    )
    loop = AgentLoop(
        session_id="loop-session",
        project_path=project,
        feed=feed,
        registry=ToolRegistry(),
        tool_context=context,
        agent_settings=AgentSettings(max_iterations=max_iterations),
    )
    return loop, feed


def test_final_answer_is_recorded_with_structured_events(project: Path) -> None:
    async def scenario() -> None:
        loop, feed = make_loop(project)
        client = ScriptedClient([ChatTurn("Done and verified.", [], "{}")])
        messages = [MessageRecord(role="assistant", content="earlier")]

        result = await loop.run(
            "fix it",
            messages,
            client,
            model="m1",
            auto_mode=True,
        )

        assert result.status is ActionStatus.SUCCESS
        assert result.final_text == "Done and verified."
        assert result.iterations == 1
        assert [event.action_type for event in feed.events()] == [
            ActionType.USER_PROMPT,
            ActionType.LLM_CALL,
            ActionType.AGENT_MESSAGE,
        ]
        assert all(event.status is ActionStatus.SUCCESS for event in feed.events())
        assert [message.role for message in messages] == ["assistant", "user", "assistant"]
        assert messages[-1].content == "Done and verified."
        assert client.requests[0][0]["role"] == "system"
        assert client.requests[0][0]["content"].startswith("You are DoctorCode")

    asyncio.run(scenario())


def test_tool_call_round_trip_is_verified_by_real_file(project: Path) -> None:
    async def scenario() -> None:
        loop, feed = make_loop(project)
        client = ScriptedClient(
            [
                ChatTurn(
                    "",
                    [
                        ToolCall(
                            id="call-1",
                            name="write_file",
                            arguments={"path": "made.txt", "content": "yes\n"},
                            raw_arguments='{"path": "made.txt", "content": "yes\\n"}',
                        )
                    ],
                    "{}",
                ),
                ChatTurn("Created made.txt.", [], "{}"),
            ]
        )
        messages: list[MessageRecord] = []

        result = await loop.run(
            "create a file",
            messages,
            client,
            model="m1",
            auto_mode=True,
        )

        assert result.status is ActionStatus.SUCCESS
        assert (project / "made.txt").read_text(encoding="utf-8") == "yes\n"
        tool_events = [
            event for event in feed.events() if event.action_type is ActionType.WRITE_FILE
        ]
        assert len(tool_events) == 1
        assert tool_events[0].status is ActionStatus.SUCCESS
        assert tool_events[0].metadata["tool"] == "write_file"
        assert messages[1].tool_calls[0]["id"] == "call-1"
        assert messages[2].role == "tool"
        assert messages[2].tool_call_id == "call-1"

    asyncio.run(scenario())


def test_model_failure_is_a_failure_event(project: Path) -> None:
    async def scenario() -> None:
        loop, feed = make_loop(project)
        client = ScriptedClient([LLMError("LLM_HTTP", "endpoint refused")])
        messages: list[MessageRecord] = []

        result = await loop.run(
            "hello",
            messages,
            client,
            model="m1",
            auto_mode=True,
        )

        assert result.status is ActionStatus.FAILURE
        assert result.error_code == "LLM_HTTP"
        assert feed.events()[-1].status is ActionStatus.FAILURE
        assert feed.events()[-1].action_type is ActionType.LLM_CALL
        assert all(event.status is not ActionStatus.IN_PROGRESS for event in feed.events())

    asyncio.run(scenario())


def test_iteration_cap_fails_loudly(project: Path) -> None:
    async def scenario() -> None:
        loop, feed = make_loop(project, max_iterations=2)
        call = ToolCall(
            id="call-x",
            name="list_files",
            arguments={"pattern": "**/*"},
            raw_arguments="{}",
        )
        client = ScriptedClient(
            [
                ChatTurn("", [call], "{}"),
                ChatTurn("", [call], "{}"),
            ]
        )
        messages: list[MessageRecord] = []

        result = await loop.run(
            "never finish",
            messages,
            client,
            model="m1",
            auto_mode=True,
        )

        assert result.status is ActionStatus.FAILURE
        assert result.error_code == "MAX_ITERATIONS"
        assert "iteration limit" in feed.events()[-1].human_summary

    asyncio.run(scenario())
