"""Tool contracts and safe execution context."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core_pack import CancellationToken
from pydantic import BaseModel, Field, JsonValue

from doctorcode.engine.events import ActionStatus, ActionType
from doctorcode.settings import ConfirmationSettings

ApprovalKind = Literal["writes", "commands", "tests", "package_actions"]


class ToolError(RuntimeError):
    """Expected tool failure with a human-readable explanation."""

    def __init__(self, message: str, *, metadata: dict[str, JsonValue] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


class ApprovalRequest(BaseModel):
    """Data shown by the GUI/CLI before a dangerous tool runs."""

    action_type: ActionType
    title: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


ApprovalCallback = Callable[[ApprovalRequest], bool]


@dataclass
class ToolContext:
    """Per-session values.  No tool ever reads global app settings directly."""

    project_path: Path
    token: CancellationToken
    max_file_bytes: int = 1_500_000
    command_timeout_seconds: int = 180
    auto_mode: bool = False
    confirmations: ConfirmationSettings = field(default_factory=ConfirmationSettings)
    approval_callback: ApprovalCallback | None = None
    logger: logging.Logger | None = None

    def approve(self, request: ApprovalRequest, kind: ApprovalKind) -> bool:
        """Apply no-watch/persistent confirmation policy and ask if needed."""
        if self.auto_mode:
            return True
        if not getattr(self.confirmations, kind):
            return True
        if self.approval_callback is None:
            raise ToolError(
                "This action needs approval, but no interactive client is attached. "
                "Enable No-Watch mode or approve from the desktop."
            )
        return bool(self.approval_callback(request))


class ToolArgs(BaseModel):
    """Base for strictly validated tool argument models."""


class ToolOutcome(BaseModel):
    """Normalized event fields produced by a tool."""

    status: ActionStatus
    human_summary: str
    raw_payload: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    action_type: ActionType = ActionType.TOOL_CALL

    @classmethod
    def success(
        cls,
        summary: str,
        *,
        raw: str = "",
        metadata: dict[str, JsonValue] | None = None,
        action_type: ActionType = ActionType.TOOL_CALL,
    ) -> ToolOutcome:
        return cls(
            status=ActionStatus.SUCCESS,
            human_summary=summary,
            raw_payload=raw,
            metadata=metadata or {},
            action_type=action_type,
        )

    @classmethod
    def failure(
        cls,
        summary: str,
        *,
        raw: str = "",
        metadata: dict[str, JsonValue] | None = None,
        action_type: ActionType = ActionType.TOOL_CALL,
    ) -> ToolOutcome:
        return cls(
            status=ActionStatus.FAILURE,
            human_summary=summary,
            raw_payload=raw,
            metadata=metadata or {},
            action_type=action_type,
        )


ToolHandler = Callable[[Any, ToolContext], ToolOutcome]


@dataclass(frozen=True)
class ToolSpec:
    """A tool exposed to the model."""

    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    action_type: ActionType = ActionType.TOOL_CALL
    approval_kind: ApprovalKind | None = None
    dangerous: bool = False

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }
