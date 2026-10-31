"""The ReAct loop: model -> validated tool -> observed fact -> model."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_pack import OperationCancelledError

from doctorcode.engine.context import MessageRecord, build_system_message
from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.llm_client import ChatClient, ChatTurn, LLMError
from doctorcode.settings import AgentSettings
from doctorcode.tools import ToolRegistry
from doctorcode.tools.base import ToolContext, ToolError, ToolOutcome


@dataclass(frozen=True)
class LoopResult:
    """Terminal state of one prompt execution."""

    status: ActionStatus
    final_text: str
    iterations: int
    error_code: str | None = None


def _one_line(text: str, limit: int = 220) -> str:
    compact = " ".join(text.strip().split())
    if not compact:
        return "Model returned no text."
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class AgentLoop:
    """Execute one user prompt against a model and the tool registry."""

    def __init__(
        self,
        *,
        session_id: str,
        project_path: Path,
        feed: ActionFeed,
        registry: ToolRegistry,
        tool_context: ToolContext,
        agent_settings: AgentSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        self.session_id = session_id
        self.project_path = project_path
        self.feed = feed
        self.registry = registry
        self.tool_context = tool_context
        self.agent_settings = agent_settings
        self.logger = logger or logging.getLogger(__name__)

    def _start_event(
        self,
        action_type: ActionType,
        summary: str,
        *,
        raw: str = "",
        metadata: dict[str, Any] | None = None,
        summary_key: str = "",
        summary_params: dict[str, Any] | None = None,
    ) -> ActionEvent:
        event = ActionEvent(
            session_id=self.session_id,
            action_type=action_type,
            status=ActionStatus.IN_PROGRESS,
            human_summary=summary,
            raw_payload=raw,
            metadata=metadata or {},
            summary_key=summary_key,
            summary_params=summary_params or {},
        )
        self.feed.append(event)
        return event

    async def run(
        self,
        prompt: str,
        messages: list[MessageRecord],
        client: ChatClient,
        *,
        model: str,
        auto_mode: bool,
    ) -> LoopResult:
        """Run until the model stops calling tools, is cancelled, or hits the cap."""
        extra = self.agent_settings.system_prompt_extra
        system = build_system_message(
            project_path=str(self.project_path),
            auto_mode=auto_mode,
            extra=extra,
        )
        user_message = MessageRecord(role="user", content=prompt)
        messages.append(user_message)
        self.feed.append(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.USER_PROMPT,
                status=ActionStatus.SUCCESS,
                human_summary=_one_line(prompt, 300),
                raw_payload=prompt,
                metadata={"chars": len(prompt), "model": model},
            )
        )

        def api_messages() -> list[dict[str, Any]]:
            return [system.to_api(), *(message.to_api() for message in messages)]

        try:
            for iteration in range(1, self.agent_settings.max_iterations + 1):
                started = time.monotonic()
                call_event = self._start_event(
                    ActionType.LLM_CALL,
                    f"Waiting for model response (step {iteration})…",
                    metadata={"model": model, "step": iteration},
                    summary_key="loop.waiting",
                    summary_params={"step": iteration},
                )
                try:
                    turn = await client.complete(
                        api_messages(),
                        self.registry.openai_tools(),
                    )
                except (OperationCancelledError, asyncio.CancelledError):
                    self.feed.replace(
                        call_event.finalize(
                            status=ActionStatus.FAILURE,
                            human_summary="Model request cancelled by user.",
                            summary_key="loop.llm_cancelled",
                            duration_ms=(time.monotonic() - started) * 1000.0,
                        )
                    )
                    raise
                except LLMError as exc:
                    self.feed.replace(
                        call_event.finalize(
                            status=ActionStatus.FAILURE,
                            human_summary=f"Model request failed: {exc}",
                            summary_key="loop.llm_failed",
                            summary_params={"error": str(exc)},
                            raw_payload=str(exc),
                            metadata={
                                "model": model,
                                "step": iteration,
                                "error_code": exc.code,
                                "retryable": exc.retryable,
                            },
                            duration_ms=(time.monotonic() - started) * 1000.0,
                        )
                    )
                    return LoopResult(ActionStatus.FAILURE, "", iteration, exc.code)
                except Exception as exc:  # defensive boundary: feed must survive
                    self.logger.exception("agent.llm_unexpected")
                    self.feed.replace(
                        call_event.finalize(
                            status=ActionStatus.FAILURE,
                            human_summary=f"Unexpected model client error: {type(exc).__name__}",
                            summary_key="loop.client_error",
                            summary_params={"error_type": type(exc).__name__},
                            raw_payload=repr(exc),
                            metadata={"model": model, "step": iteration},
                            duration_ms=(time.monotonic() - started) * 1000.0,
                        )
                    )
                    return LoopResult(ActionStatus.FAILURE, "", iteration, "LLM_UNEXPECTED")

                duration = (time.monotonic() - started) * 1000.0
                self.feed.replace(
                    call_event.finalize(
                        status=ActionStatus.SUCCESS,
                        human_summary=(
                            f"Model responded in {duration / 1000:.1f}s"
                            f" ({len(turn.tool_calls)} tool call(s))."
                        ),
                        summary_key="loop.llm_ok",
                        summary_params={
                            "seconds": round(duration / 1000, 1),
                            "calls": len(turn.tool_calls),
                        },
                        raw_payload=turn.raw_payload,
                        metadata={
                            "model": model,
                            "step": iteration,
                            "finish_reason": turn.finish_reason or "",
                            "tool_calls": len(turn.tool_calls),
                            "content_chars": len(turn.content),
                        },
                        duration_ms=duration,
                    )
                )
                messages.append(self._assistant_record(turn))

                if not turn.tool_calls:
                    text = turn.content.strip()
                    if not text:
                        self.feed.append(
                            ActionEvent(
                                session_id=self.session_id,
                                action_type=ActionType.ERROR,
                                status=ActionStatus.FAILURE,
                                human_summary="The model returned an empty final answer.",
                                summary_key="loop.empty_answer",
                                raw_payload=turn.raw_payload,
                                metadata={"model": model, "step": iteration},
                            )
                        )
                        return LoopResult(
                            ActionStatus.FAILURE, "", iteration, "EMPTY_RESPONSE"
                        )
                    self.feed.append(
                        ActionEvent(
                            session_id=self.session_id,
                            action_type=ActionType.AGENT_MESSAGE,
                            status=ActionStatus.SUCCESS,
                            human_summary=_one_line(text, 300),
                            raw_payload=turn.content,
                            metadata={"model": model, "step": iteration},
                        )
                    )
                    return LoopResult(ActionStatus.SUCCESS, text, iteration)

                for call in turn.tool_calls:
                    try:
                        outcome = await self._execute_tool(call.name, call.arguments)
                    except OperationCancelledError:
                        raise
                    except ToolError as exc:
                        outcome = ToolOutcome.failure(
                            f"Tool rejected: {exc}",
                            raw=str(exc),
                            metadata={"tool": call.name, **exc.metadata},
                        )
                    except Exception as exc:  # keep the loop repairable
                        self.logger.exception("agent.tool_unexpected")
                        outcome = ToolOutcome.failure(
                            f"Tool crashed: {type(exc).__name__}",
                            raw=repr(exc),
                            metadata={"tool": call.name},
                        )
                    messages.append(
                        MessageRecord(
                            role="tool",
                            tool_call_id=call.id,
                            name=call.name,
                            content=json.dumps(
                                {
                                    "status": outcome.status.value,
                                    "summary": outcome.human_summary,
                                    "raw_payload": outcome.raw_payload,
                                    "metadata": outcome.metadata,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
        except OperationCancelledError:
            self.feed.append(
                ActionEvent(
                    session_id=self.session_id,
                    action_type=ActionType.ERROR,
                    status=ActionStatus.FAILURE,
                    human_summary="Run stopped by user.",
                    summary_key="session.run_stopped",
                    metadata={"cancelled": True},
                )
            )
            return LoopResult(ActionStatus.FAILURE, "", 0, "CANCELLED")

        self.feed.append(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.ERROR,
                status=ActionStatus.FAILURE,
                human_summary=(
                    f"Stopped after {self.agent_settings.max_iterations} model steps; "
                    "the iteration limit was reached."
                ),
                summary_key="loop.max_iterations",
                summary_params={"limit": self.agent_settings.max_iterations},
                metadata={"max_iterations": self.agent_settings.max_iterations},
            )
        )
        return LoopResult(
            ActionStatus.FAILURE,
            "",
            self.agent_settings.max_iterations,
            "MAX_ITERATIONS",
        )

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        spec = self.registry.spec(name)
        start = self._start_event(
            spec.action_type,
            f"Running tool {name}…",
            raw=json.dumps(arguments, ensure_ascii=False, indent=2),
            metadata={"tool": name, "arguments": arguments},
            summary_key="loop.tool_running",
            summary_params={"name": name},
        )
        started = time.monotonic()
        try:
            outcome = await asyncio.to_thread(
                self.registry.execute, name, arguments, self.tool_context
            )
        except (OperationCancelledError, asyncio.CancelledError):
            self.feed.replace(
                start.finalize(
                    status=ActionStatus.FAILURE,
                    human_summary=f"{name} cancelled by user.",
                    summary_key="loop.tool_cancelled",
                    summary_params={"name": name},
                    metadata={"tool": name, "cancelled": True},
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
            )
            raise
        except ToolError as exc:
            self.feed.replace(
                start.finalize(
                    status=ActionStatus.FAILURE,
                    human_summary=f"{name} rejected: {exc}",
                    summary_key="loop.tool_rejected",
                    summary_params={"name": name, "error": str(exc)},
                    raw_payload=str(exc),
                    metadata={"tool": name, **exc.metadata},
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
            )
            raise
        duration = (time.monotonic() - started) * 1000.0
        self.feed.replace(
            start.finalize(
                status=outcome.status,
                human_summary=outcome.human_summary,
                raw_payload=outcome.raw_payload,
                summary_key=outcome.summary_key,
                summary_params=outcome.summary_params,
                metadata={
                    **outcome.metadata,
                    "tool": name,
                    "arguments": arguments,
                },
                duration_ms=duration,
            )
        )
        return outcome

    @staticmethod
    def _assistant_record(turn: ChatTurn) -> MessageRecord:
        tool_calls = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments or json.dumps(call.arguments),
                },
            }
            for call in turn.tool_calls
        ]
        return MessageRecord(
            role="assistant",
            content=turn.content or None,
            tool_calls=tool_calls or None,
        )
