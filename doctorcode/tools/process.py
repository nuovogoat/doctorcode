"""Shell tools.  They are the only place where arbitrary commands are spawned,
and both paths go through core_pack.run_process with a cancellation token.
"""

from __future__ import annotations

import os
import platform
import time
from typing import Any

from core_pack import OperationCancelledError, run_process
from pydantic import Field

from doctorcode.engine.events import ActionType
from doctorcode.tools.base import (
    ApprovalRequest,
    ToolArgs,
    ToolContext,
    ToolError,
    ToolOutcome,
    ToolSpec,
)


class CommandArgs(ToolArgs):
    command: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(default="", max_length=500, description="Why this command is needed.")


class TestArgs(ToolArgs):
    command: str = Field(min_length=1, max_length=20_000)
    reason: str = Field(default="", max_length=500)


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        shell = os.environ.get("COMSPEC", "cmd.exe")
        return [shell, "/d", "/s", "/c", command]
    return ["/bin/sh", "-c", command]


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TERM"] = "dumb"
    return env


def _run(command: str, ctx: ToolContext, *, action_type: ActionType) -> ToolOutcome:
    approval = ApprovalRequest(
        action_type=action_type,
        title=command,
        details={
            "command": command,
            "cwd": str(ctx.project_path),
            "timeout_seconds": ctx.command_timeout_seconds,
        },
    )
    kind = "tests" if action_type is ActionType.RUN_TESTS else "commands"
    if not ctx.approve(approval, kind):
        return ToolOutcome.failure(
            "Command cancelled by the user.",
            metadata={"command": command, "denied": True},
            action_type=action_type,
        )

    started = time.monotonic()
    try:
        result = run_process(
            _shell_argv(command),
            timeout=float(ctx.command_timeout_seconds),
            kill_timeout=3.0,
            token=ctx.token,
            encoding="utf-8",
            errors="replace",
            cwd=str(ctx.project_path),
            env=_environment(),
            check=False,
            isolate_process_group=True,
            logger=ctx.logger,
        )
    except (OSError, ValueError) as exc:
        raise ToolError(f"Could not start the command: {exc}") from exc
    duration_ms = (time.monotonic() - started) * 1000.0
    if result.cancelled:
        raise OperationCancelledError("Command cancelled")
    stdout = str(result.stdout)
    stderr = str(result.stderr)
    payload = (
        f"$ {command}\n"
        f"cwd: {ctx.project_path}\n"
        f"exit: {result.returncode} | timed out: {result.timed_out} | "
        f"duration: {duration_ms:.0f} ms\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
    )
    metadata: dict[str, Any] = {
        "command": command,
        "cwd": str(ctx.project_path),
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_ms": round(duration_ms, 1),
        "shell": platform.system() or "unknown",
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
    }
    if result.timed_out:
        return ToolOutcome.failure(
            f"Command timed out after {ctx.command_timeout_seconds}s.",
            raw=payload,
            metadata=metadata,
            action_type=action_type,
        )
    if result.returncode != 0:
        tail = stderr.strip().splitlines()[-1][:300] if stderr.strip() else ""
        return ToolOutcome.failure(
            f"Command failed with exit code {result.returncode}." + (f" {tail}" if tail else ""),
            raw=payload,
            metadata=metadata,
            action_type=action_type,
        )
    return ToolOutcome.success(
        f"Command completed successfully (exit 0) in {duration_ms / 1000:.1f}s.",
        raw=payload,
        metadata=metadata,
        action_type=action_type,
    )


def run_command(args: CommandArgs, ctx: ToolContext) -> ToolOutcome:
    return _run(args.command, ctx, action_type=ActionType.RUN_COMMAND)


def run_tests(args: TestArgs, ctx: ToolContext) -> ToolOutcome:
    return _run(args.command, ctx, action_type=ActionType.RUN_TESTS)


def process_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="run_command",
            description=(
                "Run a shell command inside the project. Returns full stdout/stderr. "
                "Requires approval unless No-Watch is active."
            ),
            args_model=CommandArgs,
            handler=run_command,
            action_type=ActionType.RUN_COMMAND,
            approval_kind="commands",
            dangerous=True,
        ),
        ToolSpec(
            name="run_tests",
            description=(
                "Run the project's test command. Use this instead of run_command for tests "
                "so the feed distinguishes test results."
            ),
            args_model=TestArgs,
            handler=run_tests,
            action_type=ActionType.RUN_TESTS,
            approval_kind="tests",
            dangerous=True,
        ),
    ]
