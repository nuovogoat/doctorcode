"""Workspace manager: one isolated session per project folder."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from core_pack import TaskExecutor

from doctorcode.engine.context import HandoffStore, SessionStore
from doctorcode.engine.events import ActionEvent
from doctorcode.engine.feed import ActionListener
from doctorcode.engine.llm_client import ChatClientFactory, create_client
from doctorcode.engine.session import AgentSession
from doctorcode.settings import SettingsStore


class WorkspaceManager:
    """Owns project tabs and fans their events to LAN/desktop observers."""

    def __init__(
        self,
        *,
        app_data_dir: Path,
        settings_store: SettingsStore,
        executor: TaskExecutor | None = None,
        logger: logging.Logger | None = None,
        client_factory: ChatClientFactory = create_client,
    ) -> None:
        self.app_data_dir = Path(app_data_dir)
        self.settings_store = settings_store
        self.executor = executor
        self.logger = logger or logging.getLogger(__name__)
        self.client_factory = client_factory
        self.sessions: dict[str, AgentSession] = {}
        self._by_path: dict[str, str] = {}
        self._listeners: dict[int, ActionListener] = {}
        self._listener_tokens: dict[str, int] = {}
        self._next_listener = 1
        self._lock = threading.RLock()
        self.session_store = SessionStore(self.app_data_dir)
        self.handoff_store = HandoffStore(self.app_data_dir)

    def open_project(self, folder: str | Path) -> AgentSession:
        path = Path(folder).expanduser().resolve()
        if not path.is_dir():
            raise ValueError(f"Not a project folder: {path}")
        key = str(path).casefold()
        with self._lock:
            existing_id = self._by_path.get(key)
            if existing_id and existing_id in self.sessions:
                return self.sessions[existing_id]
            restored = self.session_store.load(path)
            session = AgentSession(
                project_path=path,
                settings_store=self.settings_store,
                session_store=self.session_store,
                handoff_store=self.handoff_store,
                logger=self.logger,
                client_factory=self.client_factory,
                restored=restored,
                event_listener=self._fanout,
            )
            self.sessions[session.session_id] = session
            self._by_path[key] = session.session_id
            if session.observer_token is not None:
                self._listener_tokens[session.session_id] = session.observer_token
        self.settings_store.remember_project(str(path))
        return session

    def get(self, session_id: str) -> AgentSession:
        with self._lock:
            try:
                return self.sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"Unknown session: {session_id}") from exc

    def list(self) -> list[AgentSession]:
        with self._lock:
            return list(self.sessions.values())

    def close_project(self, session_id: str) -> None:
        with self._lock:
            session = self.sessions.pop(session_id, None)
            if session is None:
                return
            key = str(session.project_path).casefold()
            self._by_path.pop(key, None)
            listener = self._listener_tokens.pop(session_id, None)
        if session.busy:
            session.stop()
        session.save()
        if listener is not None:
            session.unsubscribe(listener)

    def close_all(self) -> None:
        for session in self.list():
            self.close_project(session.session_id)

    def subscribe_all(self, listener: ActionListener) -> int:
        """Subscribe to events from all current and future project tabs."""
        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            return token

    def unsubscribe_all(self, token: int) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def _fanout(self, event: ActionEvent) -> None:
        with self._lock:
            listeners = list(self._listeners.values())
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                self.logger.exception("workspace.listener_failed")

    def resolve(self, session_id: str) -> AgentSession:
        return self.get(session_id)

    def shutdown(self) -> None:
        self.close_all()

    def snapshot_for_android(self) -> dict[str, Any]:
        return {
            "projects": [session.state() for session in self.list()],
        }
