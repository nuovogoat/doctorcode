"""FastAPI desktop bridge for the Android companion.

Security model: loopback is not special; the server binds the LAN, so every
control route requires a bearer token unless the user explicitly disables
pairing.  The mobile client receives action-feed facts, never provider API keys.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.websockets import WebSocketDisconnect

from doctorcode import __version__
from doctorcode.engine.events import ActionEvent
from doctorcode.engine.feed import ActionFeed
from doctorcode.engine.llm_client import ChatClientFactory, create_client
from doctorcode.engine.reviewer import ReviewerService
from doctorcode.engine.session import AgentSession, SessionBusyError
from doctorcode.engine.workspace import WorkspaceManager
from doctorcode.i18n import Translator
from doctorcode.server.discovery import ServiceAdvertiser, local_addresses
from doctorcode.server.security import PairingManager
from doctorcode.settings import SettingsStore

LOGGER = logging.getLogger("doctorcode.server")


class PairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=12)
    device_name: str = Field(default="Android", max_length=80)


class OpenProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class PromptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=200_000)
    auto: bool | None = None
    wait: bool = False


class AutoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(default="", max_length=20_000)
    material: str = Field(default="", max_length=500_000)


def _bearer(value: str | None) -> str | None:
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        return None
    return token.strip()


def _prompt_payload(session: AgentSession, result) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "status": result.status.value,
        "final_text": result.final_text,
        "iterations": result.iterations,
        "error_code": result.error_code,
    }


def create_app(
    *,
    manager: WorkspaceManager,
    settings_store: SettingsStore,
    pairing: PairingManager | None = None,
    discovery: ServiceAdvertiser | None = None,
    translator: Translator | None = None,
    reviewer_factory: ChatClientFactory = create_client,
    port: int | None = None,
) -> FastAPI:
    """Build the ASGI app; dependencies are injectable for tests and the GUI."""
    translator = translator or Translator(settings_store.settings.ui.language)
    token_file = Path(manager.app_data_dir) / "server_tokens.json"
    pairing = pairing or PairingManager(
        require_pairing=settings_store.settings.server.require_pairing,
        token_file=token_file,
        logger=LOGGER,
    )
    discovery = discovery or ServiceAdvertiser(logger=LOGGER)
    effective_port = int(port or settings_store.settings.server.port)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.port = effective_port
        if settings_store.settings.server.advertise_mdns:
            discovery.start(effective_port, pairing_required=pairing.require_pairing)
        try:
            yield
        finally:
            discovery.stop()

    app = FastAPI(
        title="DoctorCode mobile bridge",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.manager = manager
    app.state.settings_store = settings_store
    app.state.pairing = pairing
    app.state.discovery = discovery
    app.state.translator = translator
    app.state.port = effective_port
    app.state.tasks: set[asyncio.Task[Any]] = set()
    app.state.runs: dict[str, asyncio.Task[Any]] = {}

    def t(key: str, **params: object) -> str:
        return translator.t(key, **params)

    def get_session(session_id: str) -> AgentSession:
        try:
            return manager.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=t("server.project_missing")) from exc

    async def require_token(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        token = _bearer(authorization)
        if not request.app.state.pairing.authorize(token):
            raise HTTPException(
                status_code=401,
                detail=t("server.unauthorized"),
                headers={"WWW-Authenticate": "Bearer"},
            )

    Auth = Depends(require_token)

    def _consume_task(task: asyncio.Task[Any]) -> None:
        app.state.tasks.discard(task)
        for session_id, running in list(app.state.runs.items()):
            if running is task:
                app.state.runs.pop(session_id, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            LOGGER.exception("server.background_prompt_failed")

    def schedule_prompt(
        session: AgentSession,
        prompt: str,
        *,
        auto: bool | None = None,
    ) -> asyncio.Task[Any]:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("server.prompt_empty")
        running = app.state.runs.get(session.session_id)
        if (running is not None and not running.done()) or session.busy:
            raise SessionBusyError("server.busy")
        if auto is not None:
            session.set_auto_mode(bool(auto))
        task = asyncio.create_task(
            session.run_prompt(prompt),
            name=f"doctorcode-prompt-{session.session_id[:8]}",
        )
        app.state.tasks.add(task)
        app.state.runs[session.session_id] = task
        task.add_done_callback(_consume_task)
        return task

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "app": "DoctorCode",
            "version": __version__,
            "pairing_required": app.state.pairing.require_pairing,
            "projects": len(manager.list()),
        }

    @app.post("/api/pair")
    async def pair(body: PairBody) -> dict[str, Any]:
        if not app.state.pairing.require_pairing:
            return {
                "ok": True,
                "token": "",
                "pairing_required": False,
                "server": "DoctorCode",
                "version": __version__,
            }
        result = app.state.pairing.pair(body.code, body.device_name)
        if result.failure == "locked":
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "error": t("server.pairing_locked"),
                    "code": "PAIRING_LOCKED",
                    "retry_after_seconds": result.retry_after_seconds,
                },
                headers={"Retry-After": str(max(1, int(result.retry_after_seconds)))},
            )
        if not result.ok:
            raise HTTPException(status_code=401, detail=t("server.bad_pairing"))
        return {
            "ok": True,
            "token": result.token,
            "pairing_required": True,
            "server": "DoctorCode",
            "version": __version__,
        }

    @app.get("/api/settings", dependencies=[Auth])
    async def public_settings() -> dict[str, Any]:
        settings = settings_store.settings
        return {
            "settings": settings.sanitized(),
            "server": app.state.pairing.status(),
            "discovery": app.state.discovery.snapshot(),
        }

    @app.get("/api/projects", dependencies=[Auth])
    async def list_projects() -> dict[str, Any]:
        projects = [session.state() for session in manager.list()]
        return {"projects": projects, "count": len(projects)}

    @app.post("/api/projects", dependencies=[Auth])
    async def open_project(body: OpenProjectBody) -> dict[str, Any]:
        path = Path(body.path).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(status_code=404, detail=t("server.project_missing"))
        session = manager.open_project(path)
        return {"project": session.state()}

    @app.get("/api/projects/{session_id}", dependencies=[Auth])
    async def project_state(session_id: str) -> dict[str, Any]:
        session = get_session(session_id)
        return {"project": session.state()}

    @app.get("/api/projects/{session_id}/events", dependencies=[Auth])
    async def project_events(
        session_id: str,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        session = get_session(session_id)
        return {
            "session_id": session.session_id,
            "events": [event.to_payload() for event in session.feed.latest(limit)],
            "count": len(session.feed),
        }

    @app.post("/api/projects/{session_id}/prompt", dependencies=[Auth])
    async def send_prompt(session_id: str, body: PromptBody) -> Any:
        session = get_session(session_id)
        prompt = body.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail=t("server.prompt_empty"))
        if body.wait:
            running = app.state.runs.get(session.session_id)
            if (running is not None and not running.done()) or session.busy:
                raise HTTPException(status_code=409, detail=t("server.busy"))
            if body.auto is not None:
                session.set_auto_mode(bool(body.auto))
            result = await session.run_prompt(prompt)
            return {
                "accepted": True,
                "completed": True,
                "result": _prompt_payload(session, result),
                "project": session.state(),
            }
        try:
            schedule_prompt(session, prompt, auto=body.auto)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=t("server.prompt_empty")) from exc
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=t("server.busy")) from exc
        return JSONResponse(
            status_code=202,
            content={
                "accepted": True,
                "completed": False,
                "project": session.state(),
            },
        )

    @app.post("/api/projects/{session_id}/stop", dependencies=[Auth])
    async def stop_prompt(session_id: str) -> dict[str, Any]:
        session = get_session(session_id)
        stopped = session.stop()
        return {"stopped": stopped, "project": session.state()}

    @app.post("/api/projects/{session_id}/auto", dependencies=[Auth])
    async def set_auto(session_id: str, body: AutoBody) -> dict[str, Any]:
        session = get_session(session_id)
        session.set_auto_mode(body.enabled)
        return {"project": session.state()}

    @app.post("/api/projects/{session_id}/model", dependencies=[Auth])
    async def switch_model(session_id: str, body: ModelBody) -> dict[str, Any]:
        session = get_session(session_id)
        try:
            snapshot = session.switch_model(body.model)
        except SessionBusyError as exc:
            raise HTTPException(status_code=409, detail=t("server.busy")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "project": session.state(),
            "handoff": {
                "snapshot_id": snapshot.snapshot_id,
                "from_model": snapshot.from_model,
                "to_model": snapshot.to_model,
                "messages": len(snapshot.messages),
                "changed_files": len(snapshot.workspace.changed_files),
            },
        }

    @app.post("/api/projects/{session_id}/review", dependencies=[Auth])
    async def review_project(session_id: str, body: ReviewBody) -> dict[str, Any]:
        session = get_session(session_id)
        material = body.material.strip() or session.review_material()
        feed = ActionFeed()
        service = ReviewerService(
            settings_store=settings_store,
            feed=feed,
            client_factory=reviewer_factory,
            logger=LOGGER,
        )
        result = await service.review(material, body.instructions)
        return {
            "status": result.status.value,
            "text": result.text,
            "model": result.model,
            "error_code": result.error_code,
            "events": [event.to_payload() for event in feed.events()],
        }

    def snapshot_payload() -> dict[str, Any]:
        projects = [session.state() for session in manager.list()]
        events = {
            session.session_id: [event.to_payload() for event in session.feed.latest(50)]
            for session in manager.list()
        }
        return {
            "type": "snapshot",
            "version": __version__,
            "pairing_required": app.state.pairing.require_pairing,
            "projects": projects,
            "events": events,
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        token = websocket.query_params.get("token") or _bearer(
            websocket.headers.get("authorization")
        )
        if not app.state.pairing.authorize(token):
            await websocket.close(code=4401, reason=t("server.unauthorized"))
            return
        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

        def enqueue(item: dict[str, Any]) -> None:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(item)

        def on_event(event: ActionEvent) -> None:
            item = {"type": "event", "event": event.to_payload()}
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(enqueue, item)

        listener_token = manager.subscribe_all(on_event)

        async def sender() -> None:
            try:
                while True:
                    item = await queue.get()
                    await websocket.send_json(item)
            except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
                return
            except Exception:
                LOGGER.exception("server.websocket_sender_failed")

        try:
            await websocket.send_json(
                {
                    "type": "hello",
                    "app": "DoctorCode",
                    "version": __version__,
                    "pairing_required": app.state.pairing.require_pairing,
                }
            )
            enqueue(snapshot_payload())
            sender_task = asyncio.create_task(sender(), name="doctorcode-ws-sender")
            try:
                while True:
                    try:
                        message = await websocket.receive_json()
                    except (json.JSONDecodeError, ValueError):
                        enqueue(
                            {
                                "type": "error",
                                "code": "BAD_JSON",
                                "message": t("server.unknown_message"),
                            }
                        )
                        continue
                    if not isinstance(message, dict):
                        enqueue(
                            {
                                "type": "error",
                                "code": "BAD_MESSAGE",
                                "message": t("server.unknown_message"),
                            }
                        )
                        continue
                    kind = str(message.get("type", ""))
                    if kind == "ping":
                        enqueue({"type": "pong"})
                        continue
                    if kind == "list_projects":
                        enqueue(snapshot_payload())
                        continue
                    if kind == "stop":
                        try:
                            session = get_session(str(message.get("session_id", "")))
                        except HTTPException:
                            enqueue(
                                {
                                    "type": "error",
                                    "code": "PROJECT_MISSING",
                                    "message": t("server.project_missing"),
                                }
                            )
                            continue
                        stopped = session.stop()
                        enqueue(
                            {
                                "type": "ack",
                                "action": "stop",
                                "session_id": session.session_id,
                                "stopped": stopped,
                            }
                        )
                        continue
                    if kind == "set_auto":
                        try:
                            session = get_session(str(message.get("session_id", "")))
                        except HTTPException:
                            enqueue(
                                {
                                    "type": "error",
                                    "code": "PROJECT_MISSING",
                                    "message": t("server.project_missing"),
                                }
                            )
                            continue
                        session.set_auto_mode(bool(message.get("enabled", False)))
                        enqueue(
                            {
                                "type": "ack",
                                "action": "set_auto",
                                "session_id": session.session_id,
                            }
                        )
                        continue
                    if kind == "prompt":
                        try:
                            session = get_session(str(message.get("session_id", "")))
                        except HTTPException:
                            enqueue(
                                {
                                    "type": "error",
                                    "code": "PROJECT_MISSING",
                                    "message": t("server.project_missing"),
                                }
                            )
                            continue
                        prompt = str(message.get("prompt", "")).strip()
                        if not prompt:
                            enqueue(
                                {
                                    "type": "error",
                                    "code": "PROMPT_EMPTY",
                                    "message": t("server.prompt_empty"),
                                }
                            )
                            continue
                        auto = message.get("auto")
                        try:
                            schedule_prompt(
                                session,
                                prompt,
                                auto=auto if isinstance(auto, bool) else None,
                            )
                        except SessionBusyError:
                            enqueue(
                                {
                                    "type": "error",
                                    "code": "BUSY",
                                    "message": t("server.busy"),
                                }
                            )
                            continue
                        enqueue(
                            {
                                "type": "ack",
                                "action": "prompt",
                                "session_id": session.session_id,
                                "accepted": True,
                            }
                        )
                        continue
                    enqueue(
                        {
                            "type": "error",
                            "code": "UNKNOWN_MESSAGE",
                            "message": t("server.unknown_message"),
                        }
                    )
            finally:
                sender_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender_task
        except WebSocketDisconnect:
            return
        finally:
            manager.unsubscribe_all(listener_token)

    return app


def run_server(
    *,
    runtime,
    project: Path,
    host: str = "0.0.0.0",
    port: int | None = None,
) -> int:
    """Run the LAN bridge until Ctrl+C; used by ``doctorcode --server``."""
    from doctorcode.i18n import Translator

    settings_store = runtime.settings_store
    translator = Translator(settings_store.settings.ui.language)
    effective_port = int(port or settings_store.settings.server.port)
    manager = WorkspaceManager(
        app_data_dir=runtime.data_dir,
        settings_store=settings_store,
        executor=runtime.executor,
        logger=runtime.core.logger,
    )
    manager.open_project(project)
    pairing = PairingManager(
        require_pairing=settings_store.settings.server.require_pairing,
        token_file=Path(runtime.data_dir) / "server_tokens.json",
        logger=runtime.core.logger,
    )
    app = create_app(
        manager=manager,
        settings_store=settings_store,
        pairing=pairing,
        translator=translator,
        port=effective_port,
    )
    print(translator.t("server.started"), flush=True)
    if pairing.require_pairing:
        print(translator.t("cli.server_pairing", code=pairing.code), flush=True)
    else:
        print(translator.t("mobile.no_pairing_warning"), flush=True)
    for address in ["127.0.0.1", *local_addresses()]:
        print(f"  http://{address}:{effective_port}/api/health", flush=True)
    try:
        uvicorn.run(app, host=host, port=effective_port, log_level="info")
    except KeyboardInterrupt:
        print(translator.t("cli.server_stopped"), file=sys.stderr)
        return 130
    except SystemExit as exc:
        return int(exc.code or 1)
    except Exception as exc:
        print(translator.t("cli.server_failed", error=exc), file=sys.stderr)
        return 1
    finally:
        manager.shutdown()
    print(translator.t("cli.server_stopped"), file=sys.stderr)
    return 0


def main() -> int:
    """``doctorcode-server`` entry point."""
    from doctorcode.cli import main as cli_main

    return cli_main(["--server", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
