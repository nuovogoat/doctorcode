"""Tool safety tests: containment, approvals, and No-Watch behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from core_pack import CancellationToken

from doctorcode.engine.events import ActionStatus, ActionType
from doctorcode.settings import ConfirmationSettings
from doctorcode.tools import ToolRegistry
from doctorcode.tools.base import ApprovalRequest, ToolContext, ToolError
from doctorcode.tools.filesystem import resolve_project_path

APPROVAL = ApprovalRequest(
    action_type=ActionType.RUN_COMMAND,
    title="echo test",
    details={"command": "echo test"},
)


def make_context(project: Path, **overrides) -> ToolContext:
    values = {
        "project_path": project,
        "token": CancellationToken(),
        "max_file_bytes": 100_000,
        "command_timeout_seconds": 10,
        "auto_mode": False,
        "confirmations": ConfirmationSettings(),
    }
    values.update(overrides)
    return ToolContext(**values)


@pytest.mark.parametrize(
    "raw",
    [
        "../outside.txt",
        "..\\outside.txt",
        "sub/../../outside.txt",
    ],
)
def test_relative_traversal_is_rejected(project: Path, raw: str) -> None:
    with pytest.raises(ToolError, match=r"stay inside|escapes"):
        resolve_project_path(project, raw)


def test_absolute_paths_are_rejected(project: Path) -> None:
    outside = project.parent / "outside.txt"
    with pytest.raises(ToolError, match="stay inside"):
        resolve_project_path(project, str(outside))


def test_writes_to_vcs_metadata_are_rejected(project: Path) -> None:
    with pytest.raises(ToolError, match="blocked"):
        resolve_project_path(project, ".git/config", for_write=True)


def test_approval_required_without_callback(project: Path) -> None:
    ctx = make_context(project)

    with pytest.raises(ToolError, match="needs approval"):
        ctx.approve(APPROVAL, "commands")


def test_no_watch_bypasses_approval(project: Path) -> None:
    calls: list[ApprovalRequest] = []
    ctx = make_context(
        project,
        auto_mode=True,
        approval_callback=lambda request: calls.append(request) or False,
    )

    assert ctx.approve(APPROVAL, "commands") is True
    assert calls == []


def test_persistent_confirmation_switch_bypasses_prompt(project: Path) -> None:
    ctx = make_context(
        project,
        confirmations=ConfirmationSettings(commands=False),
    )

    assert ctx.approve(APPROVAL, "commands") is True


def test_write_file_denied_creates_nothing(project: Path) -> None:
    requests: list[ApprovalRequest] = []
    ctx = make_context(
        project,
        approval_callback=lambda request: requests.append(request) or False,
    )
    registry = ToolRegistry()

    outcome = registry.execute(
        "write_file",
        {"path": "denied.txt", "content": "secret"},
        ctx,
    )

    assert outcome.status is ActionStatus.FAILURE
    assert outcome.metadata["denied"] is True
    assert not (project / "denied.txt").exists()
    assert len(requests) == 1
    assert requests[0].action_type is ActionType.WRITE_FILE


def test_write_file_executes_in_no_watch_mode(project: Path) -> None:
    ctx = make_context(project, auto_mode=True)
    registry = ToolRegistry()

    outcome = registry.execute(
        "write_file",
        {"path": "src/generated.txt", "content": "hello\n"},
        ctx,
    )

    assert outcome.status is ActionStatus.SUCCESS
    assert (project / "src" / "generated.txt").read_text(encoding="utf-8") == "hello\n"
    assert outcome.metadata["created"] is True


def test_registry_rejects_unknown_tool_and_bad_arguments(project: Path) -> None:
    ctx = make_context(project, auto_mode=True)
    registry = ToolRegistry()

    with pytest.raises(ToolError, match="Unknown tool"):
        registry.execute("not_a_tool", {}, ctx)
    with pytest.raises(ToolError, match="Invalid arguments"):
        registry.execute("write_file", {"content": 42}, ctx)
