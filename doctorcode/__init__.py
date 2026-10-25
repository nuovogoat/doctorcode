"""DoctorCode: model-agnostic control room for prompt engineers.

The package deliberately keeps the GUI off the import path.  Both the CLI and
the server must work in a headless environment; Tkinter/Qt are loaded only by
their explicit entry points.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
