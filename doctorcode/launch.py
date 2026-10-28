"""Desktop launcher that honours the persisted "hide console" preference.

``doctorcode_start.bat`` calls this module with the console interpreter.  When
the user enabled *Hide console on startup*, the launcher re-executes the GUI
with ``pythonw.exe`` and detaches, so the batch window closes immediately.
Otherwise the GUI runs in the current process and the console remains attached.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def settings_path() -> Path:
    """Return the settings file used by the GUI, without starting the runtime."""
    from doctorcode.runtime import app_data_dir

    return app_data_dir() / "config" / "settings.json"


def hide_console_enabled(path: Path | None = None) -> bool:
    """Read the preference fail-safe; a missing or broken file means "no"."""
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    ui = data.get("ui")
    return bool(ui.get("hide_console")) if isinstance(ui, dict) else False


def windowed_python(executable: str | None = None) -> str | None:
    """Return ``pythonw.exe`` next to the current interpreter, when present."""
    current = Path(executable or sys.executable)
    if current.name.casefold() == "pythonw.exe":
        return str(current)
    if current.name.casefold() != "python.exe":
        return None
    candidate = current.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else None


def _target(args: Sequence[str]) -> str:
    return "doctorcode.ui.qt_app" if "--qt" in args else "doctorcode.ui.tk_app"


def _forward_args(args: Sequence[str]) -> list[str]:
    return [arg for arg in args if arg != "--qt"]


def _spawn_detached(python: str, module: str, args: Sequence[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
    subprocess.Popen(
        [python, "-m", module, *args],
        close_fds=True,
        creationflags=flags,
        cwd=str(Path.cwd()),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    module = _target(args)
    forwarded = _forward_args(args)
    if hide_console_enabled():
        python = windowed_python()
        if python is not None:
            try:
                _spawn_detached(python, module, forwarded)
            except OSError:
                pass
            else:
                return 0
    if module == "doctorcode.ui.qt_app":
        from doctorcode.ui.qt_app import main as qt_main

        return qt_main(forwarded)
    from doctorcode.ui.tk_app import main as tk_main

    return tk_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
