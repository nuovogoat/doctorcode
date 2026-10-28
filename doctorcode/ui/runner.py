"""One dedicated asyncio loop for Tkinter, isolated from widget threads."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any, TypeVar

T = TypeVar("T")


class AsyncRunner:
    """Run model calls off the Tk main thread without blocking the UI.

    Futures are ``concurrent.futures.Future`` objects, safe to poll with
    ``Tk.after``.  Shutdown cancels outstanding tasks and joins the loop.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._closed = False
        self._lock = threading.RLock()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="doctorcode-asyncio",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("The GUI asyncio loop did not start.")

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            try:
                pending = [task for task in asyncio.all_tasks(self._loop) if not task.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                self.logger.exception("ui.async_cleanup_failed")
            self._loop.close()

    def submit(self, coroutine: Coroutine[Any, Any, T]) -> Future[T]:
        with self._lock:
            if self._closed:
                coroutine.close()
                raise RuntimeError("The GUI asyncio loop is closed.")
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future

    def submit_tracked(self, coroutine: Coroutine[Any, Any, T]) -> Future[T]:
        """Submit and retain the wrapping task so shutdown can cancel it."""

        async def wrapper() -> T:
            task = asyncio.current_task()
            if task is not None:
                with self._lock:
                    self._tasks.add(task)
            try:
                return await coroutine
            finally:
                if task is not None:
                    with self._lock:
                        self._tasks.discard(task)

        return self.submit(wrapper())

    def shutdown(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
