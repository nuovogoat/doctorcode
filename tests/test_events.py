"""Action feed contract tests: status is authoritative, summaries are not."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from doctorcode.engine.events import (
    ActionEvent,
    ActionStatus,
    ActionType,
    success_summary_mismatch,
)
from doctorcode.engine.feed import ActionFeed


def make_event(**overrides) -> ActionEvent:
    data = {
        "session_id": "session-1",
        "action_type": ActionType.RUN_TESTS,
        "status": ActionStatus.SUCCESS,
        "human_summary": "Tests passed.",
    }
    data.update(overrides)
    return ActionEvent(**data)


def test_failure_with_positive_summary_is_flagged() -> None:
    event = make_event(
        status=ActionStatus.FAILURE,
        human_summary="All tests passed successfully.",
    )

    assert event.has_status_summary_conflict is True
    assert success_summary_mismatch(event) is True


def test_negative_or_neutral_summary_is_not_flagged() -> None:
    failed = make_event(
        status=ActionStatus.FAILURE,
        human_summary="Tests failed with exit code 2.",
    )
    success = make_event(human_summary="All tests passed successfully.")
    in_progress = make_event(
        status=ActionStatus.IN_PROGRESS,
        human_summary="Running tests…",
    )

    assert failed.has_status_summary_conflict is False
    assert success.has_status_summary_conflict is False
    assert in_progress.has_status_summary_conflict is False


def test_event_requires_closed_status_and_non_empty_summary() -> None:
    with pytest.raises(ValidationError):
        make_event(status="OK")
    with pytest.raises(ValidationError):
        make_event(human_summary="")
    with pytest.raises(ValidationError):
        make_event(extra_field=True)


def test_event_serializes_with_aware_utc_timestamp() -> None:
    event = make_event(timestamp=datetime(2026, 1, 2, 3, 4, 5))
    payload = event.to_payload()

    assert payload["timestamp"].endswith("Z") or payload["timestamp"].endswith("+00:00")
    assert event.timestamp.tzinfo is UTC


def test_finalize_preserves_identity_and_timestamp() -> None:
    started = make_event(
        status=ActionStatus.IN_PROGRESS,
        human_summary="Running…",
    )
    finished = started.finalize(
        status=ActionStatus.FAILURE,
        human_summary="Command failed.",
        raw_payload="exit 1",
        metadata={"returncode": 1},
        duration_ms=12.5,
    )

    assert finished.event_id == started.event_id
    assert finished.timestamp == started.timestamp
    assert finished.status is ActionStatus.FAILURE
    assert finished.metadata == {"returncode": 1}
    assert finished.duration_ms == 12.5


def test_feed_replace_updates_in_place_and_replays_to_listeners() -> None:
    feed = ActionFeed()
    seen: list[tuple[str, ActionStatus]] = []
    feed.subscribe(lambda event: seen.append((event.event_id, event.status)))

    started = feed.append(make_event(status=ActionStatus.IN_PROGRESS))
    feed.replace(started.finalize(status=ActionStatus.FAILURE, human_summary="Failed."))

    assert len(feed) == 1
    assert feed.get(started.event_id).status is ActionStatus.FAILURE
    assert seen == [
        (started.event_id, ActionStatus.IN_PROGRESS),
        (started.event_id, ActionStatus.FAILURE),
    ]


def test_broken_listener_cannot_break_the_agent() -> None:
    feed = ActionFeed()

    def broken(_event: ActionEvent) -> None:
        raise RuntimeError("listener exploded")

    seen: list[str] = []
    feed.subscribe(broken)
    feed.subscribe(lambda event: seen.append(event.event_id))

    event = feed.append(make_event())

    assert [item.event_id for item in feed.events()] == [event.event_id]
    assert seen == [event.event_id]
