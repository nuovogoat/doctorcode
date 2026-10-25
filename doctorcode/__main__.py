"""Allow ``python -m doctorcode`` to open the default (Tkinter) window."""

from __future__ import annotations


def main() -> int:
    """Launch the default GUI lazily, keeping headless imports clean."""
    from doctorcode.ui.tk_app import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
