"""Pure action-feed formatting shared by Tk views and GUI tests."""

from __future__ import annotations

import json
from typing import Any, Protocol

from doctorcode.engine.events import ActionEvent, ActionStatus


class Locale(Protocol):
    def t(self, key: str, **params: object) -> str: ...


def event_time(event: ActionEvent) -> str:
    try:
        return event.timestamp.astimezone().strftime("%H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "--:--:--"


def event_status(event: ActionEvent) -> str:
    return event.status.value


def event_action(event: ActionEvent) -> str:
    return event.action_type.value


def event_human_row(event: ActionEvent) -> tuple[str, str, str, str]:
    return (
        event_time(event),
        event_status(event),
        event_action(event),
        event.human_summary,
    )


def warning_text(event: ActionEvent, translator: Locale) -> str:
    return translator.t("feed.warning") if event.has_status_summary_conflict else ""


def _metadata_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def key_value_rows(event: ActionEvent) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [
        ("event_id", event.event_id),
        ("timestamp", event.timestamp.isoformat()),
        ("session_id", event.session_id),
        ("action_type", event.action_type.value),
        ("status", event.status.value),
        ("human_summary", event.human_summary),
        ("duration_ms", "" if event.duration_ms is None else f"{event.duration_ms:.1f}"),
        ("raw_payload_bytes", str(len(event.raw_payload.encode("utf-8")))),
    ]
    for key in sorted(event.metadata):
        rows.append((key, _metadata_value(event.metadata[key])))
    return rows


def raw_text(event: ActionEvent, translator: Locale) -> str:
    return event.raw_payload or translator.t("feed.raw_empty")


def contradiction(event: ActionEvent) -> bool:
    return event.has_status_summary_conflict


def status_style(event: ActionEvent) -> str:
    if event.has_status_summary_conflict:
        return "conflict"
    if event.status is ActionStatus.FAILURE:
        return "failure"
    if event.status is ActionStatus.IN_PROGRESS:
        return "progress"
    return "success"


def selected_action_material(event: ActionEvent, *, prefer_raw: bool = True) -> str:
    """Exact material for the reviewer, with a compact fallback."""
    if prefer_raw and event.raw_payload.strip():
        return event.raw_payload
    return (
        f"[{event.status.value}] {event.action_type.value}: {event.human_summary}\n"
        f"{json.dumps(event.metadata, ensure_ascii=False, default=str, indent=2)}"
    )
