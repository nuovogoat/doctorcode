"""Optional PySide6 interface entry point.

The default contour never imports Qt.  ``doctorcode --qt`` lands here, and a
friendly, translated error is printed when the optional ``qt`` extra is not
installed.  The implementation lives in :mod:`doctorcode.ui.qt_window`, which
imports PySide6 eagerly because it is only loaded after this guard succeeds.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from doctorcode.i18n import Translator


def qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    if not qt_available():
        language = ""
        try:
            from doctorcode.runtime import app_data_dir
            from doctorcode.settings import SettingsStore

            path = app_data_dir() / "config" / "settings.json"
            if path.is_file():
                language = SettingsStore(path).settings.ui.language
        except Exception:
            language = ""
        print(Translator(language).t("gui.qt_missing"), file=sys.stderr)
        return 1
    from doctorcode.ui.qt_window import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
