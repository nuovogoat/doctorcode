"""Thread-safe approval bridge between agent threads and the Tk main loop."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field

from doctorcode.tools.base import ApprovalRequest


@dataclass(eq=False)
class PendingApproval:
    request: ApprovalRequest
    decided: threading.Event = field(default_factory=threading.Event)
    approved: bool = False

    def resolve(self, approved: bool) -> None:
        self.approved = bool(approved)
        self.decided.set()


class ApprovalBroker:
    """Agents block here; only the Tk thread opens dialogs and resolves them."""

    def __init__(self) -> None:
        self.queue: queue.Queue[PendingApproval] = queue.Queue()
        self._closing = threading.Event()
        self._pending: set[PendingApproval] = set()
        self._lock = threading.RLock()

    def request(self, request: ApprovalRequest) -> bool:
        pending = PendingApproval(request=request)
        with self._lock:
            if self._closing.is_set():
                return False
            self._pending.add(pending)
        try:
            self.queue.put(pending)
            while not pending.decided.wait(0.2):
                if self._closing.is_set():
                    return False
            return pending.approved
        finally:
            with self._lock:
                self._pending.discard(pending)

    def next_pending(self) -> PendingApproval | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        with self._lock:
            self._closing.set()
            pending = list(self._pending)
        for item in pending:
            item.resolve(False)
