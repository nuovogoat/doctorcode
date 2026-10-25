# DoctorCode

**A model-agnostic control room for prompt engineers.** DoctorCode keeps the
technical mess out of your way while leaving every raw fact one click away.
The desktop is the compute host; the Android companion is the remote control.

> Status: implementation is in progress. The repository follows the
> universal_core `TOOLING_STANDARDS_ver2.7.md` contract. See
> `DECISIONS.md` for the documented GUI and pairing decisions.

## What it is

- **Project-aware tabs.** Every tab is bound to a real folder. Empty,
  nameless sessions do not exist; a tab is named after the project directory.
- **Structured action feed.** Every action is a strict Pydantic event with
  `status`, `human_summary`, `metadata`, and `raw_payload`, shown in Human,
  Key-Value, and Raw views. A “success” sentence cannot mask a failed status.
- **No hardcoded models.** Any OpenAI-compatible endpoint works. Models are
  listed dynamically from the endpoint; writer and reviewer profiles are
  selected in the UI.
- **No-Watch mode.** Let the agent run and repair its own failures while you
  step away.
- **Context handoff.** Switching models writes a JSON snapshot and continues
  from the same point.
- **Async reviewer.** Attach a file or a raw event, connect the reviewer to a
  different endpoint/model, and get an analysis without workspace writes.
- **Android companion.** mDNS discovery, WebSocket event streaming, prompts,
  voice input, and an immediate STOP.
- **CLI first.** Everything works headless; GUI is an optional thin shell.

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
doctorcode --help
```

Configuration lives in the platform app-data folder (on Windows:
`%LOCALAPPDATA%\nuovogoat\DoctorCode`). Open **Settings**, enter an
OpenAI-compatible base URL and key, fetch the model list, and choose a model.

```powershell
# Headless run
python -m doctorcode.cli --project C:/Projects/my_project --prompt "Fix the failing tests" --auto

# Desktop GUI (Tkinter, default; no Qt dependency)
doctorcode_start.bat

# Optional Qt view
pip install -e ".[qt]"
doctorcode_qt_start.bat
```

The repository is MIT licensed. No GPL code is copied or linked.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Android companion](android/README.md)
- [Security and network endpoints](docs/SECURITY.md)
- [Tooling standards pointer](STANDARDS.md)
- [Decisions](DECISIONS.md)
- [Regressions](REGRESSIONS.md)
