"""Kimi Code session import through the shared open_code_ai_connect library.

The library owns parsing and the ``doctorcode.session`` v1 contract.  This
module only decides where the verbatim snapshot is stored, which project tab
receives the main-agent history, and how duplicate imports are suppressed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_pack import atomic_write, ensure_private_dir
from open_code_ai_connect import (
    FORMAT,
    SCHEMA_VERSION,
    KimiMigrator,
    KimiSourceError,
)

from doctorcode.engine.context import MessageRecord
from doctorcode.engine.workspace import WorkspaceManager

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
SESSION_ROLES = {"user", "assistant", "tool"}


class KimiImportError(RuntimeError):
    """A source folder, project folder, or snapshot cannot be imported."""


@dataclass(frozen=True)
class KimiCandidate:
    """One converted Kimi session, ready for the preview list."""

    snapshot: dict[str, Any]
    session_id: str
    title: str
    project_path: str
    updated_at: str
    message_count: int
    subagent_count: int
    warning_count: int
    error_count: int
    warning_codes: tuple[str, ...]
    importable: bool

    @property
    def project_dir(self) -> Path | None:
        if not self.project_path:
            return None
        path = Path(self.project_path)
        return path if path.is_dir() else None


@dataclass(frozen=True)
class KimiImportResult:
    """Outcome for one snapshot selected by the user."""

    session_id: str
    title: str
    snapshot_path: Path
    project_path: Path | None
    imported_messages: int
    already_imported: bool


def _candidate(snapshot: dict[str, Any]) -> KimiCandidate:
    session = snapshot.get("session") or {}
    warnings = snapshot.get("warnings") or []
    project_path = str(session.get("project_path") or "")
    message_count = len(snapshot.get("messages") or [])
    error_count = sum(1 for item in warnings if item.get("severity") == "error")
    codes = tuple(
        sorted({str(item.get("code")) for item in warnings if item.get("code")})
    )
    existing = False
    if project_path:
        try:
            existing = Path(project_path).is_dir()
        except OSError:
            existing = False
    return KimiCandidate(
        snapshot=snapshot,
        session_id=str(session.get("id") or ""),
        title=str(session.get("title") or session.get("id") or ""),
        project_path=project_path,
        updated_at=str(session.get("updated_at") or ""),
        message_count=message_count,
        subagent_count=len(snapshot.get("subagents") or []),
        warning_count=len(warnings),
        error_count=error_count,
        warning_codes=codes,
        importable=bool(existing and message_count),
    )


def map_snapshot_messages(snapshot: dict[str, Any]) -> list[MessageRecord]:
    """Map main-agent snapshot messages to DoctorCode's strict record.

    Subagents, reasoning, usage, attachments, warnings, and plugin extensions
    stay in the verbatim snapshot.  Empty assistant steps and system messages
    are not useful for the next model call and are skipped.
    """
    mapped: list[MessageRecord] = []
    for raw in snapshot.get("messages") or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        if role not in SESSION_ROLES:
            continue
        content = raw.get("content")
        text = None if content is None else str(content)
        if role == "assistant":
            tool_calls = raw.get("tool_calls") or None
            if not (text and text.strip()) and not tool_calls:
                continue
            mapped.append(
                MessageRecord(role="assistant", content=text, tool_calls=tool_calls)
            )
            continue
        if role == "tool":
            call_id = str(raw.get("tool_call_id") or "").strip()
            if not call_id:
                continue
            mapped.append(
                MessageRecord(
                    role="tool",
                    content=text or "",
                    tool_call_id=call_id,
                    name=str(raw.get("name") or "") or None,
                )
            )
            continue
        if not text or not text.strip():
            continue
        mapped.append(MessageRecord(role="user", content=text))
    return mapped


def snapshot_filename(session_id: str) -> str:
    """Windows-safe file name for a stable snapshot id.

    Session ids contain ``:`` (``kimi-code:workspace:session``), which is not a
    legal Windows file-name character.  The snapshot content is unchanged; only
    the on-disk label is normalized.
    """
    safe = _INVALID_FILENAME.sub("_", session_id).strip(" .")
    if not safe:
        safe = "session"
    if len(safe) > 180:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        safe = f"{safe[:150]}-{digest}"
    return f"{safe}.json"


class KimiImportService:
    """Scan, persist, and merge Kimi sessions into project tabs."""

    def __init__(
        self,
        *,
        workspace: WorkspaceManager,
        data_dir: Path,
        migrator: KimiMigrator | None = None,
    ) -> None:
        self.workspace = workspace
        self.root = Path(data_dir) / "imports" / "kimi"
        ensure_private_dir(str(self.root))
        self.migrator = migrator or KimiMigrator()

    def can_handle(self, source_dir: str | Path) -> bool:
        return self.migrator.can_handle(Path(source_dir))

    def scan(self, source_dir: str | Path) -> list[KimiCandidate]:
        """Convert every session under ``source_dir`` without touching it."""
        try:
            snapshots = self.migrator.convert(Path(source_dir))
        except (KimiSourceError, OSError) as exc:
            raise KimiImportError(str(exc)) from exc
        snapshots.sort(
            key=lambda snapshot: str(
                (snapshot.get("session") or {}).get("updated_at") or ""
            ),
            reverse=True,
        )
        return [_candidate(snapshot) for snapshot in snapshots]

    def snapshot_path(self, snapshot: dict[str, Any]) -> Path:
        session = snapshot.get("session") or {}
        return self.root / snapshot_filename(str(session.get("id") or "session"))

    def save_snapshot(self, snapshot: dict[str, Any]) -> Path:
        """Persist the envelope verbatim below the private app-data folder."""
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("format") != FORMAT
            or snapshot.get("schema_version") != SCHEMA_VERSION
        ):
            raise KimiImportError("Unsupported session snapshot format.")
        target = self.snapshot_path(snapshot)
        atomic_write(
            str(target),
            json.dumps(snapshot, ensure_ascii=False, indent=2),
        )
        return target

    def import_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        project_path: str | Path | None = None,
    ) -> KimiImportResult:
        """Store the snapshot and append its main history to a project tab."""
        snapshot_path = self.save_snapshot(snapshot)
        session = snapshot.get("session") or {}
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            raise KimiImportError("The snapshot has no session id.")
        title = str(session.get("title") or session_id)
        messages = map_snapshot_messages(snapshot)
        if not messages:
            return KimiImportResult(
                session_id=session_id,
                title=title,
                snapshot_path=snapshot_path,
                project_path=None,
                imported_messages=0,
                already_imported=False,
            )

        project = self._resolve_project(session.get("project_path"), project_path)
        agent_session = self.workspace.open_project(project)
        already = session_id in agent_session.imports
        if already:
            return KimiImportResult(
                session_id=session_id,
                title=title,
                snapshot_path=snapshot_path,
                project_path=project,
                imported_messages=0,
                already_imported=True,
            )
        count = agent_session.import_messages(
            messages,
            import_id=session_id,
            title=title,
            snapshot_path=snapshot_path,
        )
        return KimiImportResult(
            session_id=session_id,
            title=title,
            snapshot_path=snapshot_path,
            project_path=project,
            imported_messages=count,
            already_imported=False,
        )

    @staticmethod
    def _resolve_project(
        snapshot_path: object,
        override: str | Path | None,
    ) -> Path:
        raw = override if override is not None else snapshot_path
        if raw is None or not str(raw).strip():
            raise KimiImportError(
                "The Kimi session has no project folder; choose one to import into."
            )
        path = Path(str(raw)).expanduser()
        try:
            path = path.resolve()
        except OSError as exc:
            raise KimiImportError(f"Invalid project folder: {path}") from exc
        if not path.is_dir():
            raise KimiImportError(f"Project folder does not exist: {path}")
        return path
