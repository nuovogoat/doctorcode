"""Workspace-scoped file tools built on core_pack atomic writes."""

from __future__ import annotations

import hashlib
from pathlib import Path

from core_pack import atomic_write
from pydantic import Field

from doctorcode.engine.events import ActionType
from doctorcode.tools.base import ToolArgs, ToolContext, ToolError, ToolOutcome


class _PathArgs(ToolArgs):
    path: str = Field(description="Path relative to the project root.")


class ListFilesArgs(ToolArgs):
    pattern: str = Field(default="**/*", description="Glob relative to project root.")
    max_results: int = Field(default=200, ge=1, le=2000)


class ReadFileArgs(_PathArgs):
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=400, ge=1, le=4000)


class WriteFileArgs(_PathArgs):
    content: str = Field(description="Complete UTF-8 file content.")
    overwrite: bool = Field(default=True)


class ReplaceInFileArgs(_PathArgs):
    old_text: str = Field(min_length=1)
    new_text: str = ""
    count: int = Field(default=1, ge=1, le=100)


_BLOCKED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}


def resolve_project_path(project: Path, raw: str, *, for_write: bool = False) -> Path:
    """Resolve a path and prove it stays inside the project.

    Writes additionally reject VCS metadata and dependency/cache trees.
    Symlinks are resolved before the containment check.
    """
    if not raw or "\x00" in raw:
        raise ToolError("A non-empty relative path is required.")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ToolError("The path must stay inside the project root.")
    root = project.resolve()
    target = (root / candidate).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ToolError("The path escapes the project root.") from exc
    if for_write:
        if target.exists() and target.is_dir():
            raise ToolError("The target path is a directory.")
        if any(part in _BLOCKED_PARTS for part in target.relative_to(root).parts):
            raise ToolError("Writes to VCS, dependency, and cache directories are blocked.")
    return target


def _relative(project: Path, target: Path) -> str:
    try:
        return target.relative_to(project.resolve()).as_posix()
    except ValueError:
        return str(target)


def list_files(args: ListFilesArgs, ctx: ToolContext) -> ToolOutcome:
    root = ctx.project_path.resolve()
    if Path(args.pattern).is_absolute() or ".." in Path(args.pattern).parts:
        raise ToolError("The glob must be relative and may not contain '..'.")
    rows: list[str] = []
    skipped = 0
    for candidate in root.glob(args.pattern):
        if not candidate.is_file():
            continue
        try:
            candidate.resolve().relative_to(root)
        except (OSError, ValueError):
            skipped += 1
            continue
        if any(part in _BLOCKED_PARTS for part in candidate.relative_to(root).parts):
            continue
        rows.append(candidate.relative_to(root).as_posix())
        if len(rows) >= args.max_results:
            break
    rows.sort()
    payload = "\n".join(rows)
    return ToolOutcome.success(
        f"Found {len(rows)} file(s) matching {args.pattern}.",
        raw=payload,
        metadata={
            "pattern": args.pattern,
            "count": len(rows),
            "skipped": skipped,
            "truncated": len(rows) >= args.max_results,
        },
        action_type=ActionType.TOOL_CALL,
        summary_key="tool.found",
        summary_params={"count": len(rows), "pattern": args.pattern},
    )


def read_file(args: ReadFileArgs, ctx: ToolContext) -> ToolOutcome:
    target = resolve_project_path(ctx.project_path, args.path)
    if not target.exists() or not target.is_file():
        raise ToolError(f"File not found: {args.path}")
    size = target.stat().st_size
    if size > ctx.max_file_bytes:
        raise ToolError(
            f"File is larger than the configured limit ({size} > {ctx.max_file_bytes} bytes).",
            metadata={"path": args.path, "size": size},
        )
    raw = target.read_bytes()
    binary = b"\x00" in raw[:4096]
    if binary:
        raise ToolError("Binary files cannot be read as text.", metadata={"path": args.path})
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError("The file is not valid UTF-8.", metadata={"path": args.path}) from exc
    lines = text.splitlines()
    start = args.start_line - 1
    selected = lines[start : start + args.max_lines]
    body = "\n".join(selected)
    if start + args.max_lines < len(lines):
        body += "\n… [truncated by read_file; read a later range]"
    return ToolOutcome.success(
        f"Read {len(selected)} line(s) from {args.path}.",
        raw=body,
        metadata={
            "path": args.path,
            "size": size,
            "line_start": args.start_line,
            "lines_returned": len(selected),
            "total_lines": len(lines),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        action_type=ActionType.READ_FILE,
        summary_key="tool.read",
        summary_params={"lines": len(selected), "path": args.path},
    )


def write_file(args: WriteFileArgs, ctx: ToolContext) -> ToolOutcome:
    target = resolve_project_path(ctx.project_path, args.path, for_write=True)
    if target.exists() and not args.overwrite:
        raise ToolError(f"File already exists: {args.path}")
    encoded = args.content.encode("utf-8")
    if len(encoded) > ctx.max_file_bytes:
        raise ToolError(
            f"Content exceeds the configured limit ({len(encoded)} > {ctx.max_file_bytes} bytes)."
        )
    approved = ctx.approve(
        _approval_for(ActionType.WRITE_FILE, args.path, args.content),
        "writes",
    )
    if not approved:
        return ToolOutcome.failure(
            "Write cancelled by the user.",
            metadata={"path": args.path, "denied": True},
            action_type=ActionType.WRITE_FILE,
            summary_key="tool.write_denied",
        )
    existed = target.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(str(target), encoded)
    return ToolOutcome.success(
        f"{'Overwrote' if existed else 'Created'} {args.path}.",
        raw=args.content,
        metadata={
            "path": args.path,
            "bytes": len(encoded),
            "created": not existed,
            "sha256": hashlib.sha256(encoded).hexdigest(),
        },
        action_type=ActionType.WRITE_FILE,
        summary_key="tool.write_overwrote" if existed else "tool.write_created",
        summary_params={"path": args.path},
    )


def replace_in_file(args: ReplaceInFileArgs, ctx: ToolContext) -> ToolOutcome:
    target = resolve_project_path(ctx.project_path, args.path, for_write=True)
    if not target.exists() or not target.is_file():
        raise ToolError(f"File not found: {args.path}")
    if target.stat().st_size > ctx.max_file_bytes:
        raise ToolError("File is larger than the configured limit.")
    text = target.read_text(encoding="utf-8")
    occurrences = text.count(args.old_text)
    if occurrences == 0:
        raise ToolError("old_text was not found; no change was made.")
    if occurrences < args.count:
        raise ToolError(
            f"Expected at least {args.count} occurrence(s), found {occurrences}."
        )
    approved = ctx.approve(
        _approval_for(
            ActionType.WRITE_FILE,
            args.path,
            f"{args.count} replacement(s); old_text={args.old_text[:300]!r}",
        ),
        "writes",
    )
    if not approved:
        return ToolOutcome.failure(
            "Edit cancelled by the user.",
            metadata={"path": args.path, "denied": True},
            action_type=ActionType.WRITE_FILE,
            summary_key="tool.edit_denied",
        )
    updated = text.replace(args.old_text, args.new_text, args.count)
    atomic_write(str(target), updated.encode("utf-8"))
    return ToolOutcome.success(
        f"Replaced {args.count} occurrence(s) in {args.path}.",
        raw=f"--- old ---\n{args.old_text}\n--- new ---\n{args.new_text}",
        metadata={
            "path": args.path,
            "replacements": args.count,
            "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        },
        action_type=ActionType.WRITE_FILE,
        summary_key="tool.replaced",
        summary_params={"count": args.count, "path": args.path},
    )


def _approval_for(action: ActionType, path: str, content: str):
    from doctorcode.tools.base import ApprovalRequest

    return ApprovalRequest(
        action_type=action,
        title=f"{action.value}: {path}",
        details={"path": path, "preview": content[:600]},
    )


def filesystem_tools() -> list[object]:
    """Return tool specs consumed by the registry."""
    from doctorcode.tools.base import ToolSpec

    return [
        ToolSpec(
            name="list_files",
            description="List project files matching a relative glob. Read-only.",
            args_model=ListFilesArgs,
            handler=list_files,
        ),
        ToolSpec(
            name="read_file",
            description="Read a UTF-8 text file range inside the project.",
            args_model=ReadFileArgs,
            handler=read_file,
        ),
        ToolSpec(
            name="write_file",
            description="Create or overwrite a UTF-8 text file. Requires approval.",
            args_model=WriteFileArgs,
            handler=write_file,
            action_type=ActionType.WRITE_FILE,
            approval_kind="writes",
            dangerous=True,
        ),
        ToolSpec(
            name="replace_in_file",
            description="Replace exact text in a file. Requires approval.",
            args_model=ReplaceInFileArgs,
            handler=replace_in_file,
            action_type=ActionType.WRITE_FILE,
            approval_kind="writes",
            dangerous=True,
        ),
    ]
