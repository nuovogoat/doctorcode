"""Poll a concurrent future on the Qt event loop."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, QTimer


class FutureWatcher(QObject):
    """Poll an asyncio/executor future without touching Qt from a worker."""

    def __init__(
        self,
        parent: QObject,
        future: Future[Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
        *,
        interval: int = 100,
    ) -> None:
        super().__init__(parent)
        self._future = future
        self._on_done = on_done
        self._on_error = on_error
        self._timer = QTimer(self)
        self._timer.setInterval(interval)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self) -> None:
        if not self._future.done():
            return
        self._timer.stop()
        try:
            value = self._future.result()
        except Exception as exc:
            self._on_error(exc)
        else:
            self._on_done(value)
        finally:
            self.deleteLater()
