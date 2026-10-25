"""Session context, handoff snapshots, and durable session state.

Handoff means: the next model receives the same normalized message history,
the same tool-state summary, and a real snapshot of uncommitted workspace
files.  It is not a chat-title change.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core_pack import atomic_write, ensure_private_dir, run_process
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from doctorcode.engine.events import ActionEvent


class MessageRecord(BaseModel):
    """JSON-safe chat message; tool calls stay in protocol shape."""

    model_config = ConfigDict(extra="forbid")

    role: str
    content: str | None = None
    tool_calls: list[dict[str, JsonValue]] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_api(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        return data


class HandoffFile(BaseModel):
    """One changed file captured for a model handoff."""

    model_config = ConfigDict(extra="forbid")

    path: str
    status: str = ""
    size: int = 0
    sha256: str = ""
    binary: bool = False
    truncated: bool = False
    content: str | None = None


class WorkspaceState(BaseModel):
    """Git status, patch, and uncommitted-file contents at handoff time."""

    model_config = ConfigDict(extra="forbid")

    is_git_repo: bool = False
    status_text: str = ""
    diff: str = ""
    changed_files: list[HandoffFile] = Field(default_factory=list)
    files_omitted: int = 0


class HandoffSnapshot(BaseModel):
    """Durable JSON snapshot used when switching models."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    snapshot_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_id: str
    project_path: str
    project_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reason: str = "model_switch"
    from_model: str = ""
    to_model: str = ""
    messages: list[MessageRecord] = Field(default_factory=list)
    tool_state: dict[str, JsonValue] = Field(default_factory=dict)
    workspace: WorkspaceState = Field(default_factory=WorkspaceState)
    event_count: int = 0


class StoredSession(BaseModel):
    """Persisted project tab state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    session_id: str
    project_path: str
    project_name: str
    model: str = ""
    auto_mode: bool = False
    messages: list[MessageRecord] = Field(default_factory=list)
    events: list[ActionEvent] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def project_key(project_path: str | Path) -> str:
    """Stable path key (case-folded on Windows) for session files."""
    normalized = os.path.normcase(os.path.realpath(str(project_path)))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _run_git(project: Path, args: list[str]) -> tuple[int, str, str]:
    git = shutil.which("git") or "git"
    try:
        result = run_process(
            [git, "-C", str(project), *args],
            timeout=20.0,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, ValueError):
        return 127, "", ""
    return result.returncode, str(result.stdout), str(result.stderr)


def inspect_workspace(
    project: Path,
    *,
    max_files: int = 100,
    max_file_bytes: int = 262_144,
) -> WorkspaceState:
    """Capture real uncommitted state without invoking a shell."""
    code, status_text, _ = _run_git(project, ["status", "--porcelain=v1", "--untracked-files=all"])
    if code != 0:
        return WorkspaceState(is_git_repo=False)
    _, diff, _ = _run_git(project, ["diff", "--no-ext-diff", "--unified=3", "--"])
    changed: list[HandoffFile] = []
    omitted = 0
    for line in status_text.splitlines():
        if not line:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1]
        path = Path(raw_path.strip().strip('"'))
        absolute = (project / path).resolve()
        try:
            absolute.relative_to(project.resolve())
        except ValueError:
            omitted += 1
            continue
        if not absolute.is_file():
            continue
        try:
            size = absolute.stat().st_size
            payload = absolute.read_bytes()
        except OSError:
            omitted += 1
            continue
        binary = b"\x00" in payload[:4096]
        truncated = size > max_file_bytes or binary
        content = None
        if not truncated:
            try:
                content = payload.decode("utf-8")
            except UnicodeDecodeError:
                binary = True
                truncated = True
        if len(changed) >= max_files:
            omitted += 1
            continue
        changed.append(
            HandoffFile(
                path=path.as_posix(),
                status=line[:2],
                size=size,
                sha256=hashlib.sha256(payload).hexdigest(),
                binary=binary,
                truncated=truncated,
                content=content,
            )
        )
    return WorkspaceState(
        is_git_repo=True,
        status_text=status_text,
        diff=diff,
        changed_files=changed,
        files_omitted=omitted,
    )


class HandoffStore:
    """Writes handoff JSON below the private app-data directory."""

    def __init__(self, base_dir: Path) -> None:
        self.root = Path(base_dir) / "handoffs"
        ensure_private_dir(str(self.root))

    def save(self, snapshot: HandoffSnapshot) -> Path:
        target_dir = self.root / project_key(snapshot.project_path)
        ensure_private_dir(str(target_dir))
        name = f"{snapshot.created_at.strftime('%Y%m%dT%H%M%S')}-{snapshot.snapshot_id}.json"
        target = target_dir / name
        atomic_write(
            str(target),
            json.dumps(
                snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
            ),
        )
        return target

    def load(self, path: str | Path) -> HandoffSnapshot:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return HandoffSnapshot.model_validate(data)

    def list_for_project(self, project_path: str | Path) -> list[Path]:
        target = self.root / project_key(project_path)
        if not target.exists():
            return []
        return sorted(target.glob("*.json"), reverse=True)


class SessionStore:
    """Loads/saves one project session; corrupt files fall back to a fresh tab."""

    def __init__(self, base_dir: Path) -> None:
        self.root = Path(base_dir) / "sessions"
        ensure_private_dir(str(self.root))

    def _path(self, project_path: str | Path) -> Path:
        return self.root / f"{project_key(project_path)}.json"

    def load(self, project_path: str | Path) -> StoredSession | None:
        path = self._path(project_path)
        if not path.exists():
            return None
        try:
            return StoredSession.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            with contextlib.suppress(OSError):
                path.replace(path.with_suffix(f".broken-{stamp}.json"))
            return None

    def save(self, state: StoredSession) -> Path:
        state.updated_at = datetime.now(UTC)
        target = self._path(state.project_path)
        atomic_write(
            str(target),
            state.model_dump_json(indent=2),
        )
        return target

    def delete(self, project_path: str | Path) -> None:
        path = self._path(project_path)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


_SYSTEM_PROMPT = """\
You are DoctorCode, an engineering agent operating inside a real project folder.
The user is a prompt engineer: they command the work but are not required to
read source code. Be precise, keep the action feed honest, and never claim a
command succeeded without checking its result.

Rules:
1. Inspect before editing. Read relevant files and preserve unrelated work.
2. Use only the provided tools. Paths are relative to the project root and may
   not escape it. Never modify .git or dependency caches.
3. If a command fails, read stdout/stderr and diagnose before retrying.
4. Do not claim success without evidence. Finish with a short human summary
   and the concrete verification you performed.
5. Never ask the user to paste secrets. Never echo API keys.
6. Do not invent provider names, model names, URLs, or test results.
"""


def build_system_message(
    *,
    project_path: str,
    auto_mode: bool,
    extra: str = "",
) -> MessageRecord:
    prompt = _SYSTEM_PROMPT
    prompt += f"\nProject root: {project_path}\n"
    if auto_mode:
        prompt += (
            "No-Watch mode is active: work autonomously, fix your own failures, "
            "and do not ask questions unless the task is impossible or destructive.\n"
        )
    else:
        prompt += "Ask for approval when the harness requests it.\n"
    if extra:
        prompt += f"\nUser instructions:\n{extra}\n"
    return MessageRecord(role="system", content=prompt)
