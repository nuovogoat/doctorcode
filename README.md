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

## Connecting an AI model and themes

DoctorCode ships no built-in providers: it works with any OpenAI-compatible
endpoint that you configure yourself.

1. Open **Settings** — the toolbar button (or the menu, or `Ctrl+,`).
2. In the **Writer** section enter the endpoint *Base URL* (for example
   `https://api.example.com/v1`) and the *API key* if the endpoint needs one.
3. Press **Fetch models**: DoctorCode calls `GET {base_url}/models` and fills
   the list. Save the settings.
4. Choose a model in the toolbar list for the active project tab. The
   **Reviewer** section configures the second, read-only profile the same way.

Until a Base URL is entered, a start-up hint window repeats these steps, and
the model list shows a "model not configured" placeholder instead of looking
broken. API keys stay in the local settings file and are never sent to the
Android companion.

The same Settings window switches the **Theme** between **Dark** and
**Light**; the change applies immediately to every open window. The interface
language (ru/en/de) switches with the flag buttons in the toolbar.

## Android companion

The companion requires Android 15 (API 35) and is a separate Gradle project in
[`android/`](android/README.md). It discovers the desktop over mDNS, pairs with
the six-digit code, streams the action feed, sends prompts and STOP, and supports
optional voice input.

```powershell
cd android
.\gradlew.bat :app:assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

Build requirements: JDK 17, Android SDK Platform 35, Build-Tools 35.0.0. Set
`ANDROID_HOME` or create `android/local.properties` with `sdk.dir`. The complete
pairing flow, API mapping, permissions, and troubleshooting are in the
[companion guide](android/README.md). The network and threat model is in
[Security](docs/SECURITY.md).


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

## LAN bridge

The server is opt-in (`python -m doctorcode.cli --server --project <folder>` or
**Mobile access** in the GUI). When pairing is enabled, every control route
requires `Authorization: Bearer <token>`; only `/api/health` and `/api/pair` are
public. The phone receives sanitized state and feed events, never API keys.

| Surface | Endpoints |
|---|---|
| Health/pairing | `GET /api/health`, `POST /api/pair` |
| Projects | `GET/POST /api/projects`, `GET /api/projects/{id}` |
| Feed | `GET /api/projects/{id}/events` |
| Actions | `POST .../prompt`, `.../stop`, `.../auto`, `.../model`, `.../review` |
| Streaming | `WS /ws` (`hello`, `snapshot`, `event`, `ack`, `error`, `pong`) |
| Discovery | mDNS `_doctorcode._tcp.local.` with `version`, `pairing`, `path` |

The full registry, token handling, and cleartext-LAN limitations are documented
in [Security](docs/SECURITY.md). Provider traffic goes only to the Base URL the
user configured: `GET {base_url}/models` for the catalogue and the endpoint's
chat-completions route for runs.

## Allowed network endpoints

Every domain DoctorCode or its build may contact is listed here line by line.
A request outside this list requires explicit user consent first. There is no
telemetry, analytics, or crash reporting anywhere in the product.

Desktop runtime:

- `<user-configured base URL host>` — the OpenAI-compatible provider endpoint
  entered in Settings; used for `GET {base_url}/models` and chat completions,
  and only after the user configures it.
- `224.0.0.251:5353` — mDNS multicast for `_doctorcode._tcp.local.` while
  Mobile access is enabled; link-local, never routed to the internet.

Android companion runtime:

- mDNS multicast on the connected Wi-Fi network (`_doctorcode._tcp.`).
- The paired desktop's private LAN address only; `NetworkPolicy` rejects
  public IPs and arbitrary DNS names before OkHttp is called.

Installation (`pip install`):

- `github.com`, `codeload.github.com` — git dependencies `universal_core`
  v0.8.2 and `open_code_ai_connect` v2.0.0.
- `pypi.org`, `files.pythonhosted.org` — PyPI packages.

Android build (Gradle in `android/`):

- `services.gradle.org` — Gradle 8.9 wrapper distribution.
- `dl.google.com` — Android SDK packages, Android Gradle Plugin, Google Maven.
- `repo.maven.apache.org` — Maven Central (Kotlin, OkHttp, kotlinx).
- `plugins.gradle.org` — Gradle Plugin Portal metadata.

CI (self-hosted Windows runner):

- `github.com`, `objects.githubusercontent.com` — pinned actions, repository
  traffic, on-demand Python builds for the toolcache; private git dependencies
  are cloned over git+ssh (`github.com` port 22) with the read-only deploy
  keys described under Verification and gates.
- `pypi.org`, `files.pythonhosted.org` — Python dependencies.
- `aka.ms`, `download.visualstudio.microsoft.com` — Microsoft OpenJDK 17.
- `dl.google.com` — Android cmdline-tools, platform 35, build-tools 35.0.0.
- `services.gradle.org`, `repo.maven.apache.org`, `plugins.gradle.org` —
  Android build dependencies.

## Verification and gates

```powershell
.\.venv\Scripts\python.exe tools\check_gates.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check doctorcode tests tools
.\.venv\Scripts\python.exe -m pip check
```

`check_gates.py` enforces i18n parity and placeholders, Android string parity,
launcher rules, one product version across Python/mDNS/Android, regression-test
links, PowerShell BOMs, documentation links, and Android manifest security
attributes. CI (`.github/workflows/ci.yml`) runs the same gates, the
commit-subject check, the Python suite, Ruff, `pip check`, and the Android
`testDebugUnitTest`/`assembleDebug`/`lintDebug` tasks under Microsoft OpenJDK 17
on the self-hosted Windows runner described in [RUNNERS.md](RUNNERS.md); the
repository itself neither installs nor registers runners.

The Python dependencies `universal_core` and `open_code_ai_connect` are private
git repositories, so the CI Install step authenticates with read-only deploy
keys stored as Actions secrets: `CI_DEPLOY_KEY_CORE` and
`CI_DEPLOY_KEY_CONNECT` (the `doctorcode-ci (read-only)` keys in each
dependency repo's Settings → Deploy keys). Alternative: `CI_DEPENDENCIES_TOKEN`,
a fine-grained PAT with **Contents: read** on both repos — if present, it takes
precedence. Without any of these the Install step fails fast with an explicit
instruction instead of hanging the runner on a credential prompt. Set or rotate
them with:

```powershell
gh secret set CI_DEPLOY_KEY_CORE --repo nuovogoat/doctorcode
gh secret set CI_DEPLOY_KEY_CONNECT --repo nuovogoat/doctorcode
```

Revoking access means deleting the `doctorcode-ci (read-only)` deploy key from
the corresponding dependency repository (no personal credentials are involved).

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Android companion](android/README.md)
- [Security and network endpoints](docs/SECURITY.md)
- [Tooling standards pointer](STANDARDS.md)
- [Decisions](DECISIONS.md)
- [Regressions](REGRESSIONS.md)
- [CI runners](RUNNERS.md)
