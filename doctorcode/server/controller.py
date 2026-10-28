"""Start/stop uvicorn in a background thread for the desktop mobile window."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn

from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.i18n import Translator
from doctorcode.server.api import create_app
from doctorcode.server.discovery import local_addresses
from doctorcode.server.security import PairingManager
from doctorcode.settings import SettingsStore


@dataclass
class ServerInfo:
    host: str
    port: int
    pairing_code: str
    pairing_required: bool
    urls: list[str] = field(default_factory=list)


class MobileServerController:
    """One managed uvicorn instance per desktop process.

    The server shares the desktop ``WorkspaceManager`` so the phone sees the
    same project tabs, action feed, history, and approvals as the PC.
    """

    def __init__(
        self,
        *,
        manager: WorkspaceManager,
        settings_store: SettingsStore,
        translator: Translator,
        logger: logging.Logger | None = None,
    ) -> None:
        self.manager = manager
        self.settings_store = settings_store
        self.translator = translator
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._pairing: PairingManager | None = None
        self._info: ServerInfo | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(
                self._thread
                and self._thread.is_alive()
                and self._server
                and self._server.started
            )

    @property
    def info(self) -> ServerInfo | None:
        with self._lock:
            return self._info

    @property
    def pairing(self) -> PairingManager | None:
        with self._lock:
            return self._pairing

    def start(self, *, host: str = "0.0.0.0", port: int | None = None) -> ServerInfo:
        with self._lock:
            if self.running:
                assert self._info is not None
                return self._info
            settings = self.settings_store.settings
            effective_port = int(port if port is not None else settings.server.port)
            pairing = PairingManager(
                require_pairing=settings.server.require_pairing,
                token_file=Path(self.manager.app_data_dir) / "server_tokens.json",
                logger=self.logger,
            )
            app = create_app(
                manager=self.manager,
                settings_store=self.settings_store,
                pairing=pairing,
                translator=self.translator,
                port=effective_port,
            )
            config = uvicorn.Config(
                app,
                host=host,
                port=effective_port,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(
                target=self._serve,
                args=(server,),
                name="doctorcode-mobile-server",
                daemon=True,
            )
            self._server = server
            self._pairing = pairing
            self._thread = thread
            thread.start()
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                if server.started:
                    break
                if not thread.is_alive():
                    break
                time.sleep(0.05)
            if not server.started:
                server.should_exit = True
                thread.join(timeout=2.0)
                self._server = None
                self._thread = None
                self._pairing = None
                self._info = None
                raise RuntimeError("The mobile server could not bind its port.")
            actual_port = self._actual_port(server, effective_port)
            urls = [
                f"http://{address}:{actual_port}"
                for address in ["127.0.0.1", *local_addresses()]
            ]
            info = ServerInfo(
                host=host,
                port=actual_port,
                pairing_code=pairing.code,
                pairing_required=pairing.require_pairing,
                urls=urls,
            )
            self._info = info
            return info

    @staticmethod
    def _actual_port(server: uvicorn.Server, fallback: int) -> int:
        try:
            sockets = server.servers[0].sockets
            if sockets:
                return int(sockets[0].getsockname()[1])
        except (AttributeError, IndexError, OSError):
            pass
        return fallback

    def _serve(self, server: uvicorn.Server) -> None:
        try:
            asyncio.run(server.serve())
        except Exception:
            self.logger.exception("server.thread_failed")

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._info = None
            self._pairing = None
        if server is None:
            return
        server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=6.0)
        with self._lock:
            if self._thread is thread:
                self._server = None
                self._thread = None
