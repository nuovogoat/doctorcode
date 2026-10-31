"""Strict, structured action-feed events.

The feed is the product's honesty boundary: a friendly sentence never controls
what the UI reports.  ``status`` is authoritative; a contradictory summary is
flagged visually.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


class ActionStatus(StrEnum):
    """Closed status vocabulary required by the product brief."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    IN_PROGRESS = "IN_PROGRESS"


class ActionType(StrEnum):
    """Machine-readable action kinds used by the three feed views."""

    SESSION = "session"
    USER_PROMPT = "user_prompt"
    AGENT_MESSAGE = "agent_message"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    RUN_COMMAND = "run_command"
    RUN_TESTS = "run_tests"
    HANDOFF = "handoff"
    REVIEW = "review"
    SERVER = "server"
    ERROR = "error"


_SUCCESS_HINTS = re.compile(
    r"\b("
    r"all (?:good|green|ok|clear|passed)|"
    r"success(?:ful(?:ly)?)?|succeeded|"
    r"everything (?:is )?(?:fine|ok|good)|no errors?|"
    r"вс[её] (?:ок|хорошо|чисто|зел[её]но)|"
    r"успешн\w*|готово|без ошибок|тесты прошли|"
    r"alles (?:ok|gut|grün)|erfolgreich|keine fehler"
    r")\b",
    re.IGNORECASE,
)
_FAILURE_HINTS = re.compile(
    r"\b(fail(?:ed|ure|ing)?|error|exception|denied|cancelled|canceled|"
    r"ошибк\w*|провал\w*|упал\w*|отмен\w*|запрещ\w*|"
    r"fehler|gescheitert|abgebrochen)\b",
    re.IGNORECASE,
)


class ActionEvent(BaseModel):
    """One immutable-by-convention action record.

    The model is *strict*: new fields must be added deliberately.  `metadata`
    is JSON-only so two hosts (desktop/Android) can consume exactly the same
    contract.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str
    action_type: ActionType
    status: ActionStatus
    human_summary: str = Field(min_length=1)
    raw_payload: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    duration_ms: float | None = Field(default=None, ge=0.0)
    # Stable i18n key plus JSON-only parameters: UIs render the summary in the
    # selected language while `human_summary` stays the English fallback for
    # logs, the model context, and clients without the key (old saved events
    # simply omit both fields and keep rendering the fallback).
    summary_key: str = ""
    summary_params: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def has_status_summary_conflict(self) -> bool:
        """True when a positive summary contradicts a failure status."""
        if self.status is not ActionStatus.FAILURE:
            return False
        summary = self.human_summary
        positive = bool(_SUCCESS_HINTS.search(summary))
        negative = bool(_FAILURE_HINTS.search(summary))
        return positive and not negative

    def finalize(
        self,
        *,
        status: ActionStatus,
        human_summary: str,
        raw_payload: str = "",
        metadata: dict[str, JsonValue] | None = None,
        duration_ms: float | None = None,
        summary_key: str | None = None,
        summary_params: dict[str, JsonValue] | None = None,
    ) -> ActionEvent:
        """Return a completed copy while preserving identity and timestamp."""
        data = self.model_dump()
        data.update(
            {
                "status": status,
                "human_summary": human_summary,
                "raw_payload": raw_payload,
                "metadata": metadata if metadata is not None else self.metadata,
                "duration_ms": duration_ms,
                "summary_key": summary_key if summary_key is not None else self.summary_key,
                "summary_params": (
                    summary_params if summary_params is not None else self.summary_params
                ),
            }
        )
        return ActionEvent.model_validate(data)

    def to_payload(self) -> dict[str, Any]:
        """API-safe JSON representation (dates are ISO 8601 UTC)."""
        return self.model_dump(mode="json")


def success_summary_mismatch(event: ActionEvent) -> bool:
    """Compatibility helper used by UI tests and bridges."""
    return event.has_status_summary_conflict
