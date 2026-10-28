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
  selected in the UI. The catalogue is read by the shared
  `open_code_ai_connect` library, which knows only a Base URL and an API key.
- **Kimi Code import.** Pick a `.kimi-code` data folder, preview every session,
  and append selected main-agent histories to their project tabs. The original
  session stays untouched; a verbatim universal snapshot is saved locally.
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
`%LOCALAPPDATA%\DoctorCode`). Open **Settings**, enter an
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

## Provider loading

Open **Settings**, enter the Base URL of an OpenAI-compatible endpoint, and
paste the API key. **Refresh models** calls `GET {base_url}/models` through
`open_code_ai_connect.ProviderManager` and fills the model picker with the
sorted, de-duplicated result. Nothing about a provider — host, name, or model
identifier — is compiled into DoctorCode.

The key is stored only in the local private settings file. It is not written
into provider configuration files, import snapshots, logs, or the Android
handoff; the companion receives only the public profile data.

## Importing Kimi Code sessions

Use **File → Import Kimi sessions…** in either desktop shell:

1. Choose the folder that contains `.kimi-code` (the user home folder also
   works).
2. DoctorCode scans every `sessions/<workspace>/session_*` tree without
   modifying it and shows title, project path, message count, and warnings.
3. Select sessions and import. A session whose project folder is missing asks
   for a replacement folder.
4. The full `doctorcode.session` v1 envelope is saved verbatim below
   `%LOCALAPPDATA%\DoctorCode\imports\kimi\`; only meaningful main-agent
   `user`, `assistant`, and `tool` messages are appended to the project tab.

Re-importing the same session into the same project is a no-op: DoctorCode
remembers the stable snapshot id in the project state. Subagents, reasoning,
usage, attachments, warnings, and plugin extensions remain available in the
snapshot but are not flattened into the model conversation.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Android companion](android/README.md)
- [Security and network endpoints](docs/SECURITY.md)
- [Tooling standards pointer](STANDARDS.md)
- [Decisions](DECISIONS.md)
- [Regressions](REGRESSIONS.md)
