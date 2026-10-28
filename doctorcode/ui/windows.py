"""Single-instance draggable Toplevel windows opened from the menu."""

from __future__ import annotations

import contextlib
import tkinter as tk
from collections.abc import Callable

from doctorcode.i18n import Translator


class WindowManager:
    def __init__(self, root: tk.Tk, translator: Translator) -> None:
        self.root = root
        self.translator = translator
        self._windows: dict[str, tk.Toplevel] = {}

    def open(self, key: str, factory: Callable[[], tk.Toplevel]) -> tk.Toplevel:
        existing = self._windows.get(key)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.focus_set()
                    return existing
            except tk.TclError:
                pass
        window = factory()
        self._windows[key] = window

        def forget() -> None:
            if self._windows.get(key) is window:
                self._windows.pop(key, None)
            with contextlib.suppress(tk.TclError):
                window.destroy()

        window.protocol("WM_DELETE_WINDOW", forget)
        return window

    def get(self, key: str) -> tk.Toplevel | None:
        window = self._windows.get(key)
        try:
            return window if window is not None and window.winfo_exists() else None
        except tk.TclError:
            return None

    def all(self) -> list[tk.Toplevel]:
        return [window for window in self._windows.values() if self.get_id(window)]

    def get_id(self, window: tk.Toplevel) -> bool:
        try:
            return bool(window.winfo_exists())
        except tk.TclError:
            return False

    def close_all(self) -> None:
        for window in list(self._windows.values()):
            with contextlib.suppress(tk.TclError):
                window.destroy()
        self._windows.clear()
