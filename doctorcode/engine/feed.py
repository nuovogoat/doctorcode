"""Thread-safe structured action feed and its listeners."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Protocol

from doctorcode.engine.events import ActionEvent


class ActionListener(Protocol):
    def __call__(self, event: ActionEvent) -> None: ...


class ActionFeed:
    """Append/update action records and fan them out to subscribers.

    Listeners are invoked outside the feed lock.  A faulty listener must never
    break the engine or another listener; errors are isolated per callback.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[ActionEvent] = []
        self._by_id: dict[str, int] = {}
        self._listeners: dict[int, ActionListener] = {}
        self._next_listener = 1

    def append(self, event: ActionEvent) -> ActionEvent:
        with self._lock:
            self._by_id[event.event_id] = len(self._events)
            self._events.append(event)
        self._notify(event)
        return event

    def replace(self, event: ActionEvent) -> ActionEvent:
        """Replace an existing event by id; append if it vanished."""
        with self._lock:
            index = self._by_id.get(event.event_id)
            if index is None or index >= len(self._events):
                self._by_id[event.event_id] = len(self._events)
                self._events.append(event)
            else:
                self._events[index] = event
        self._notify(event)
        return event

    def events(self) -> list[ActionEvent]:
        with self._lock:
            return list(self._events)

    def latest(self, limit: int = 200) -> list[ActionEvent]:
        if limit <= 0:
            return []
        with self._lock:
            return list(self._events[-limit:])

    def get(self, event_id: str) -> ActionEvent | None:
        with self._lock:
            index = self._by_id.get(event_id)
            if index is None:
                return None
            return self._events[index]

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def __iter__(self) -> Iterator[ActionEvent]:
        return iter(self.events())

    def subscribe(self, listener: ActionListener) -> int:
        with self._lock:
            token = self._next_listener
            self._next_listener += 1
            self._listeners[token] = listener
            return token

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._listeners.pop(token, None)

    def _notify(self, event: ActionEvent) -> None:
        with self._lock:
            listeners = list(self._listeners.values())
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Listener failures must not affect the agent.  The session's
                # core logger records them if one is available.
                continue


def subscribe_queue(
    feed: ActionFeed, put: Callable[[ActionEvent], None]
) -> tuple[int, ActionListener]:
    """Helper: bridge a feed to a thread-safe queue producer."""

    def listener(event: ActionEvent) -> None:
        put(event)

    token = feed.subscribe(listener)
    return token, listener
