"""One project tab: isolated history, model selection, cancellation, handoff."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from core_pack import CancellationToken, OperationCancelledError

from doctorcode.engine.context import (
    HandoffSnapshot,
    HandoffStore,
    MessageRecord,
    SessionStore,
    StoredSession,
    build_system_message,
    inspect_workspace,
)
from doctorcode.engine.events import ActionEvent, ActionStatus, ActionType
from doctorcode.engine.feed import ActionFeed, ActionListener
from doctorcode.engine.llm_client import ChatClientFactory, create_client
from doctorcode.engine.loop import AgentLoop, LoopResult
from doctorcode.settings import (
    AppSettings,
    ConfigError,
    SettingsStore,
)
from doctorcode.tools import ToolRegistry
from doctorcode.tools.base import ApprovalCallback, ToolContext


class SessionBusyError(RuntimeError):
    """Raised when a second prompt is started in the same project tab."""


def _short(text: str, limit: int = 300) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


class AgentSession:
    """A project-bound agent session.

    All state here belongs to exactly one folder.  Nothing is shared with
    another tab except the immutable application settings and the tool registry
    definition.
    """

    def __init__(
        self,
        *,
        project_path: Path,
        settings_store: SettingsStore,
        session_store: SessionStore,
        handoff_store: HandoffStore,
        logger: logging.Logger | None = None,
        client_factory: ChatClientFactory = create_client,
        restored: StoredSession | None = None,
        event_listener: ActionListener | None = None,
    ) -> None:
        self.project_path = project_path.resolve()
        if not self.project_path.is_dir():
            raise ValueError(f"Project folder does not exist: {self.project_path}")
        self.project_name = self.project_path.name or str(self.project_path)
        self.settings_store = settings_store
        self.session_store = session_store
        self.handoff_store = handoff_store
        self.logger = logger or logging.getLogger(__name__)
        self.client_factory = client_factory
        self.session_id = restored.session_id if restored else uuid.uuid4().hex
        self.messages: list[MessageRecord] = list(restored.messages) if restored else []
        self.imports: list[str] = list(restored.imports) if restored else []
        self.feed = ActionFeed()
        if restored:
            for event in restored.events:
                self.feed.append(event)
            self.auto_mode = restored.auto_mode
            self.model = restored.model
        else:
            self.auto_mode = False
            self.model = ""
        # Subscribe only after restored history is loaded so reopening a project
        # does not replay old events to LAN clients.  New tabs still deliver
        # their opening event to observers that subscribed before the tab existed.
        self.observer_token = (
            self.feed.subscribe(event_listener) if event_listener is not None else None
        )
        if not self.model:
            self.model = self.settings_store.settings.profile("writer").model
        self._busy = False
        self._token: CancellationToken | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._lock = threading.RLock()
        self._approval_callback: ApprovalCallback | None = None
        self.last_result: LoopResult | None = None
        if not restored:
            self._emit(
                ActionEvent(
                    session_id=self.session_id,
                    action_type=ActionType.SESSION,
                    status=ActionStatus.SUCCESS,
                    human_summary=f"Project tab opened: {self.project_name}",
                    summary_key="session.opened",
                    summary_params={"name": self.project_name},
                    raw_payload=str(self.project_path),
                    metadata={
                        "project_path": str(self.project_path),
                        "project_name": self.project_name,
                    },
                )
            )

    # ------------------------------------------------------------------ state
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            token = self._token
        return bool(token and token.is_cancelled)

    @property
    def settings(self) -> AppSettings:
        return self.settings_store.settings

    def set_approval_callback(self, callback: ApprovalCallback | None) -> None:
        with self._lock:
            self._approval_callback = callback

    def set_auto_mode(self, enabled: bool) -> None:
        with self._lock:
            if self.auto_mode == enabled:
                return
            self.auto_mode = bool(enabled)
        self._emit(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.SESSION,
                status=ActionStatus.SUCCESS,
                human_summary=(
                    "No-Watch mode enabled: actions run without prompts."
                    if enabled
                    else "No-Watch mode disabled: destructive actions ask first."
                ),
                summary_key="session.auto_on" if enabled else "session.auto_off",
                metadata={"auto_mode": bool(enabled)},
            )
        )
        self.save()

    def import_messages(
        self,
        messages: list[MessageRecord],
        *,
        import_id: str,
        title: str = "",
        snapshot_path: Path | None = None,
    ) -> int:
        """Append a converted external session once, keeping the feed honest.

        The same snapshot imported into the same project is a no-op: its stable
        ``session.id`` is remembered in ``imports``.  A different project tab
        gets its own history and its own import record.
        """
        import_id = import_id.strip()
        if not import_id:
            raise ValueError("An import id is required.")
        with self._lock:
            if self._busy:
                raise SessionBusyError("Stop the current run before importing history.")
            if import_id in self.imports:
                return 0
            clean = [message.model_copy(deep=True) for message in messages]
            self.messages.extend(clean)
            self.imports.append(import_id)
        count = len(clean)
        self._emit(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.SESSION,
                status=ActionStatus.SUCCESS,
                human_summary=(
                    f"Imported {count} message(s) from external session "
                    f"{title or import_id}."
                ),
                summary_key="session.imported",
                summary_params={"count": count, "title": title or import_id},
                raw_payload=str(snapshot_path) if snapshot_path else "",
                metadata={
                    "import_id": import_id,
                    "imported_messages": count,
                    "snapshot_path": str(snapshot_path) if snapshot_path else "",
                },
            )
        )
        self.save()
        return count

    def subscribe(self, listener: ActionListener) -> int:
        return self.feed.subscribe(listener)

    def unsubscribe(self, token: int) -> None:
        self.feed.unsubscribe(token)

    def state(self) -> dict[str, Any]:
        """Safe status snapshot for the desktop and Android clients."""
        with self._lock:
            busy = self._busy
            auto_mode = self.auto_mode
        profile = self.settings.profile("writer")
        return {
            "session_id": self.session_id,
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "model": self.model,
            "auto_mode": auto_mode,
            "busy": busy,
            "event_count": len(self.feed),
            "writer_configured": profile.is_configured,
            "reviewer_configured": self.settings.profile("reviewer").is_configured,
        }

    # ------------------------------------------------------------------ prompt
    async def run_prompt(self, prompt: str) -> LoopResult:
        """Execute one prompt to completion.  One run per tab at a time."""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt must not be empty.")
        with self._lock:
            if self._busy:
                raise SessionBusyError("This project is already running a prompt.")
            profile = self.settings.profile("writer")
            if not self.model:
                self.model = profile.model
            if not profile.base_url:
                error = ConfigError("SETTINGS_MISSING_BASE_URL")
                self._emit_config_error(error)
                return LoopResult(ActionStatus.FAILURE, "", 0, str(error))
            if not self.model:
                error = ConfigError("SETTINGS_MISSING_MODEL")
                self._emit_config_error(error)
                return LoopResult(ActionStatus.FAILURE, "", 0, str(error))
            effective = profile.model_copy(update={"model": self.model})
            self._busy = True
            token = CancellationToken()
            self._token = token
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            callback = self._approval_callback
            settings = self.settings

        client = None
        try:
            client = self.client_factory(
                effective,
                timeout=settings.agent.request_timeout_seconds,
            )
            tool_context = ToolContext(
                project_path=self.project_path,
                token=token,
                max_file_bytes=settings.agent.max_file_bytes,
                command_timeout_seconds=settings.agent.command_timeout_seconds,
                auto_mode=self.auto_mode,
                confirmations=settings.confirmations,
                approval_callback=callback,
                logger=self.logger,
            )
            loop = AgentLoop(
                session_id=self.session_id,
                project_path=self.project_path,
                feed=self.feed,
                registry=ToolRegistry(),
                tool_context=tool_context,
                agent_settings=settings.agent,
                logger=self.logger,
            )
            result = await loop.run(
                prompt,
                self.messages,
                client,
                model=self.model,
                auto_mode=self.auto_mode,
            )
            self.last_result = result
            return result
        except asyncio.CancelledError:
            self._emit(
                ActionEvent(
                    session_id=self.session_id,
                    action_type=ActionType.ERROR,
                    status=ActionStatus.FAILURE,
                    human_summary="Run stopped by user.",
                    summary_key="session.run_stopped",
                    metadata={"cancelled": True},
                )
            )
            result = LoopResult(ActionStatus.FAILURE, "", 0, "CANCELLED")
            self.last_result = result
            return result
        except OperationCancelledError:
            self._emit(
                ActionEvent(
                    session_id=self.session_id,
                    action_type=ActionType.ERROR,
                    status=ActionStatus.FAILURE,
                    human_summary="Run stopped by user.",
                    summary_key="session.run_stopped",
                    metadata={"cancelled": True},
                )
            )
            result = LoopResult(ActionStatus.FAILURE, "", 0, "CANCELLED")
            self.last_result = result
            return result
        except Exception as exc:
            self.logger.exception("session.run_failed")
            self._emit(
                ActionEvent(
                    session_id=self.session_id,
                    action_type=ActionType.ERROR,
                    status=ActionStatus.FAILURE,
                    human_summary=f"Session error: {type(exc).__name__}: {exc}",
                    summary_key="session.error",
                    summary_params={"error_type": type(exc).__name__, "error": str(exc)},
                    raw_payload=repr(exc),
                    metadata={"error_type": type(exc).__name__},
                )
            )
            result = LoopResult(ActionStatus.FAILURE, "", 0, "SESSION_ERROR")
            self.last_result = result
            return result
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    self.logger.exception("session.client_close_failed")
            with self._lock:
                self._busy = False
                self._token = None
                self._task = None
                self._loop = None
            self.save()

    def _emit_config_error(self, error: ConfigError) -> None:
        code = str(error)
        self._emit(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.ERROR,
                status=ActionStatus.FAILURE,
                human_summary=(
                    "Writer endpoint is not configured. Open Settings and choose a "
                    "base URL, model, and optional API key."
                ),
                summary_key="session.config_missing",
                metadata={"error_code": code, "settings_required": True},
            )
        )

    def stop(self) -> bool:
        """Idempotently cancel the active run.  Safe to call when idle.

        The token stops child processes; cancelling the current asyncio task
        also interrupts an in-flight HTTP request immediately.
        """
        with self._lock:
            token = self._token
            loop = self._loop
            task = self._task
        if token is None or token.is_cancelled:
            return False
        token.cancel()
        if loop is not None and task is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(task.cancel)
        return True

    def _emit(self, event: ActionEvent) -> None:
        self.feed.append(event)

    # --------------------------------------------------------------- handoff
    def _snapshot(self, reason: str, *, to_model: str) -> HandoffSnapshot:
        recent: list[dict[str, Any]] = []
        for event in self.feed.latest(30):
            recent.append(
                {
                    "event_id": event.event_id,
                    "action_type": event.action_type.value,
                    "status": event.status.value,
                    "summary": event.human_summary,
                }
            )
        workspace = inspect_workspace(self.project_path)
        return HandoffSnapshot(
            session_id=self.session_id,
            project_path=str(self.project_path),
            project_name=self.project_name,
            reason=reason,
            from_model=self.model,
            to_model=to_model,
            messages=list(self.messages),
            tool_state={"recent_actions": recent, "auto_mode": self.auto_mode},
            workspace=workspace,
            event_count=len(self.feed),
        )

    def switch_model(self, new_model: str) -> HandoffSnapshot:
        """Persist a full handoff, then switch the model for subsequent calls."""
        new_model = new_model.strip()
        if not new_model:
            raise ValueError("Model must not be empty.")
        with self._lock:
            if self._busy:
                raise SessionBusyError("Stop the current run before switching models.")
            if new_model == self.model:
                raise ValueError("The requested model is already active.")
            snapshot = self._snapshot("model_switch", to_model=new_model)
            snapshot_path = self.handoff_store.save(snapshot)
            previous = self.model
            self.model = new_model
        self._emit(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.HANDOFF,
                status=ActionStatus.SUCCESS,
                human_summary=(
                    f"Context handed off from {previous or '(none)'} to {new_model}; "
                    f"{len(snapshot.messages)} messages and "
                    f"{len(snapshot.workspace.changed_files)} changed file(s) saved."
                ),
                summary_key="handoff.switched",
                summary_params={
                    "from_model": previous or "(none)",
                    "to_model": new_model,
                    "messages": len(snapshot.messages),
                    "files": len(snapshot.workspace.changed_files),
                },
                raw_payload=str(snapshot_path),
                metadata={
                    "from_model": previous,
                    "to_model": new_model,
                    "snapshot_path": str(snapshot_path),
                    "changed_files": len(snapshot.workspace.changed_files),
                    "workspace_truncated": snapshot.workspace.files_omitted,
                },
            )
        )
        self.save()
        return snapshot

    def create_handoff(self, reason: str = "manual") -> Path:
        """Write a manual handoff snapshot without changing the model."""
        with self._lock:
            snapshot = self._snapshot(reason, to_model=self.model)
        path = self.handoff_store.save(snapshot)
        self._emit(
            ActionEvent(
                session_id=self.session_id,
                action_type=ActionType.HANDOFF,
                status=ActionStatus.SUCCESS,
                human_summary=f"Handoff snapshot saved for {self.project_name}.",
                summary_key="handoff.saved",
                summary_params={"name": self.project_name},
                raw_payload=str(path),
                metadata={"snapshot_path": str(path), "reason": reason},
            )
        )
        return path

    # ------------------------------------------------------------------ save
    def save(self) -> None:
        """Persist project history and model choice."""
        with self._lock:
            state = StoredSession(
                session_id=self.session_id,
                project_path=str(self.project_path),
                project_name=self.project_name,
                model=self.model,
                auto_mode=self.auto_mode,
                imports=list(self.imports),
                messages=list(self.messages),
                events=self.feed.events(),
            )
        try:
            self.session_store.save(state)
        except OSError:
            self.logger.exception("session.save_failed")

    def review_material(self, max_chars: int = 200_000) -> str:
        """Collect conversation + raw facts for an explicit review request."""
        lines: list[str] = [
            f"Project: {self.project_name}",
            f"Path: {self.project_path}",
            f"Model: {self.model}",
            "",
            "Conversation:",
        ]
        for message in self.messages:
            content = message.content or ""
            if message.role == "tool" and len(content) > max_chars:
                content = content[:max_chars] + "\n[truncated]"
            lines.append(f"[{message.role}] {content}")
        lines.append("\nRecent structured actions:")
        for event in self.feed.latest(80):
            lines.append(
                f"- [{event.status.value}] {event.action_type.value}: "
                f"{event.human_summary}\n{event.raw_payload[: max_chars // 20]}"
            )
        return "\n".join(lines)[-max_chars:]

    def system_context(self) -> MessageRecord:
        return build_system_message(
            project_path=str(self.project_path),
            auto_mode=self.auto_mode,
            extra=self.settings.agent.system_prompt_extra,
        )
