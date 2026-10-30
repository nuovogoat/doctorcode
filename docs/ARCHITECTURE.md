# Architecture

DoctorCode is a model-agnostic control room built from three delivery contours:
a headless engine, two optional desktop shells, and an Android companion. The
desktop is the compute host; every model call and every file write happens
there. This document describes the boundaries that keep that promise.

## 1. Process and package layout

```
doctorcode/
├── cli.py                 argparse entry point, headless runs, server mode
├── launch.py              desktop launcher, honours "hide console"
├── runtime.py             core_pack context, settings store, executor
├── settings.py            Pydantic settings, sanitized() for remote clients
├── engine/
│   ├── workspace.py       project tabs, event fan-out
│   ├── session.py         one tab: history, model, cancellation, import
│   ├── loop.py            agent loop, tool calls, repair iterations
│   ├── context.py         session/handoff persistence, message models
│   ├── events.py          strict feed vocabulary (status/action/event)
│   ├── feed.py            thread-safe event store and listeners
│   ├── llm_client.py      OpenAI-compatible factory and model catalogue
│   ├── reviewer.py        asynchronous, read-only reviewer
│   └── kimi_import.py     Kimi scan/convert/preview/merge service
├── server/
│   ├── api.py             FastAPI REST + WebSocket bridge
│   ├── controller.py      prompt scheduling and loop ownership
│   ├── security.py        pairing, bearer tokens, lockout
│   └── discovery.py       mDNS advertisement and LAN address list
├── tools/                 filesystem, process, approval callbacks
├── ui/
│   ├── tk_app.py          default Tkinter shell
│   ├── qt_app.py          optional PySide6 shell
│   ├── qt_future.py       Qt/async bridge shared by windows
│   └── ...                project tab, feed views, settings, import, mobile
└── i18n/                  ru/en/de packs, no GUI dependency
```

The GUI is never imported by `doctorcode.engine`, `doctorcode.server`, or
`doctorcode.cli`. A scheduler or CI can run the CLI on a machine without a
display.

## 2. Layering rules

1. **Infrastructure comes from `core_pack`.** Configuration primitives, process
   execution, cancellation tokens, the executor, private directories, atomic
   writes, and structured logging are not reimplemented in DoctorCode.
2. **Provider and migration protocols come from `open_code_ai_connect`.** The
   shared library owns `ProviderManager` (base URL + API key, `GET /models`) and
   `KimiMigrator` (`can_handle`, `convert`). DoctorCode owns only UI state,
   snapshot storage, tab merging, and duplicate records.
3. **Domain state is Pydantic.** `AppSettings`, `StoredSession`,
   `MessageRecord`, `HandoffSnapshot`, and `ActionEvent` validate at every
   persistence boundary. Unknown fields are rejected in settings and request
   bodies; stored session files remain forward-compatible through explicit
   defaults.
4. **The feed is authoritative.** A friendly summary cannot override a
   `FAILURE` status. `ActionStatus` is a closed vocabulary
   (`SUCCESS`, `FAILURE`, `IN_PROGRESS`); UIs render the status, not a derived
   sentiment.
5. **Project tabs are isolated.** `WorkspaceManager` owns exactly one
   `AgentSession` per resolved project folder. Sessions share immutable settings
   and the tool registry definition, never message history or cancellation
   state.

## 3. Runtime composition

`create_runtime()` creates one `core_pack.Core`, one private data directory, and
one `TaskExecutor` per process. The desktop shells, CLI, and server all receive
that same runtime object. No module creates a second thread pool or a second
settings store.

`SettingsStore` persists to `config/settings.json` below the app-data root. The
file contains two connection profiles — `writer` and `reviewer` — with a base
URL, API key, model, and temperature. Model names and URLs have no compiled-in
defaults.

`WorkspaceManager.open_project()` resolves the folder, reuses an existing tab
for the same path (case-insensitive on Windows), and otherwise restores the
last `StoredSession` for that path. A tab opened for the first time emits a
`session` event; a restored tab does not replay old history to remote clients.

## 4. Prompt lifecycle

```
UI / REST / WS
      │
      ▼
AgentSession.run_prompt()
      │  one run per tab, CancellationToken
      ▼
AgentLoop
      ├── writer profile → OpenAI-compatible chat completions
      ├── ToolRegistry: read, write, run command, run tests
      ├── approval callback (unless No-Watch is on)
      └── repair iterations up to agent.max_iterations
      │
      ▼
ActionFeed.append(event)
      ├── Tk/Qt feed views
      ├── WebSocket subscribers (all tabs)
      └── SessionStore.save()
```

`AgentSession.state()` exposes only public state: session id, project name and
path, model, No-Watch flag, busy flag, event count, and whether the writer and
reviewer profiles are configured. It never exposes keys or message bodies.
`AgentSession.imports` is the per-project list of imported stable session ids.

STOP is idempotent: `stop()` cancels the active token, marks the run cancelled,
and leaves the tab reusable. A second STOP is a no-op, not an exception.

## 5. Structured action feed

Every event carries:

- `event_id`, `timestamp`, `session_id`;
- `action_type` (closed enum: session, prompt, message, LLM call, tool call,
  file read/write, command, tests, handoff, review, error);
- `status` (`SUCCESS`, `FAILURE`, `IN_PROGRESS`);
- `human_summary`, `metadata`, `raw_payload`, optional `duration_ms`.

The feed keeps an insertion-ordered list plus an id index so a later event can
update an earlier `IN_PROGRESS` record. Listeners run outside the feed lock and
are isolated: a failing GUI callback cannot stop the engine or another client.
Three views (Human, Key-Value, Raw) are projections of the same object.

## 6. Persistence

The app-data root is `%LOCALAPPDATA%\DoctorCode` on Windows and the platform
equivalent from `core_pack` elsewhere. Tests and portable runs can override it
with `DOCTORCODE_DATA_DIR`.

```
DoctorCode/
├── config/settings.json          profiles, UI, server, confirmations
├── sessions/<hash>.json          StoredSession per project path
├── handoffs/<hash>/*.json        model-switch snapshots
├── imports/kimi/<session>.json   verbatim doctorcode.session v1 envelope
├── server_tokens.json            SHA-256 hashes of paired phone tokens
└── log/doctorcode.log            rotating structured log
```

`project_key()` is a SHA-256 prefix of the normalised, case-folded real path.
Raw project paths never become file names.

### Kimi import

`KimiImportService.scan()` calls the shared `KimiMigrator.can_handle()` on the
chosen folder and converts every accepted session with `convert()`. Preview data
contains title, project path, message count, and warnings only. Import:

1. writes the full snapshot verbatim to `imports/kimi/`;
2. maps meaningful main-agent `user`, `assistant`, and `tool` messages into
   `MessageRecord` rows (system messages, empty messages, and empty assistant
   steps are skipped);
3. resolves the target project tab by `session.project_path`, with a user
   override when the original folder no longer exists;
4. calls `AgentSession.import_messages()`, which appends once per stable
   `session.id` and records the id in `StoredSession.imports`.

Subagents, reasoning, usage, attachments, warnings, and plugin extensions stay
in the snapshot. They are not flattened into the model conversation.

## 7. Desktop shells

Tkinter is the default and only required GUI. PySide6 is an optional extra
(`pip install -e ".[qt]"`) and a separate launcher. Both shells:

- present project-bound tabs, the three feed views, settings, reviewer, mobile
  access, modules, and the Kimi import window;
- run engine work through a future/executor adapter so the event loop stays
  responsive;
- confirm destructive actions unless No-Watch is enabled;
- use the same i18n packs and the same `Translator` as the CLI.

The Qt bridge (`qt_future.py`) converts engine futures into Qt signals without
importing Qt from the engine. The import window is split the same way:
`ui/import_window.py` for Tk, `ui/qt_import.py` for Qt, with all scanning and
merging in `engine/kimi_import.py`.

## 8. LAN bridge

`doctorcode/server/api.py` exposes FastAPI routes under `/api` and a WebSocket
at `/ws`. The server binds `0.0.0.0` when started explicitly; it is not started
implicitly with the GUI unless the user enables auto-start.

- REST is used for precise request/response actions (open project, prompt,
  stop, auto, model, review).
- WebSocket carries `hello`, `snapshot`, `event`, `ack`, `error`, and `pong`
  frames. Prompts and STOP may also be sent over the socket, but the Android
  client uses REST for actions and the socket for streaming.
- `manager.subscribe_all()` feeds every project tab into every socket, so a
  phone sees the same facts as the desktop feed.
- mDNS advertises `_doctorcode._tcp.local.` with only `version`, `pairing`, and
  `path` TXT records. No code, token, key, or project path is published.

The REST and WebSocket contract is documented in
[`../android/README.md`](../android/README.md) and
[`SECURITY.md`](SECURITY.md).

## 9. Android companion

`android/` is a separate Gradle project (Kotlin 2.0.21, AGP 8.7.3, Compose,
minSdk 35). It contains no Python and no model credentials. Layers:

- `data/Models.kt` — serializable REST/WS contracts and strict frame parsing;
- `data/NetworkPolicy.kt` — private-host enforcement before any request;
- `data/DoctorCodeApi.kt` — OkHttp REST/WebSocket client;
- `data/TokenVault.kt` — Keystore AES/GCM encryption of the bearer token;
- `data/SessionStore.kt` — non-secret connection preferences;
- `data/ServerDiscovery.kt` — NSD discovery and serialized resolution;
- `ui/MainViewModel.kt` — connection, project selection, prompt/STOP/auto,
  polling, and reconnecting stream;
- `ui/DoctorCodeScreen.kt` — Compose connect screen, workspace, feed, composer.

The phone never computes a model response. It sends commands and renders the
feed. When the desktop is unreachable, the socket retries with exponential
backoff; prompts fail with a visible error instead of being queued.

## 10. Quality gates

`tools/check_gates.py` is dependency-free and runs before the test suite in CI.
It verifies:

- ru/en/de parity, placeholders, empty strings, and flags in Python packs;
- Android string parity, format specifiers, and untranslated German copies;
- `.bat` launchers contain `%~dp0`, `%*`, `chcp 65001`, and `pause`, and have no
  absolute Windows paths;
- one version across `pyproject.toml`, `doctorcode.__version__`, mDNS, and the
  Android `versionName`;
- every `REGRESSIONS.md` row points at an existing test function;
- PowerShell files with Cyrillic are UTF-8 with BOM;
- required documentation exists and README links resolve;
- the Android manifest keeps its pairing/LAN security attributes.

The Python suite covers the event contract, sessions, workspace isolation,
handoff, Kimi import, provider loading, server REST/WS, and GUI-independent
runtime behaviour. Android unit tests cover `NetworkPolicy` and the JSON frame
contract. CI also builds the Android debug APK and runs `lintDebug`.
