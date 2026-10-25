# DoctorCode — decisions

Architecture decisions that deviate from, or clarify, the v2.7 tooling standards.
The canonical standard remains `TOOLING_STANDARDS_ver2.7.md`.

## D-001 — Tkinter is the default GUI; Qt is optional

**Decision:** `doctorcode_start.bat` opens the Tkinter GUI. PySide6 lives in
`doctorcode/ui/qt_app.py` behind the optional `qt` extra.

**Why:** v2.7 §2 and §13 forbid making PySide6 a mandatory dependency. The
product brief asked for PySide6; both views are shipped, but only Tkinter is
part of the default contour.

**Consequence:** the Qt UI can be started with `doctorcode_qt_start.bat` or
`python -m doctorcode.ui.qt_app` after installing `.[qt]`.

## D-002 — `core_pack` is used for infrastructure, not for product state

**Decision:** `core_pack` owns configuration primitives, process execution,
cancellation, the executor, paths, atomic file writes, and structured logging.
DoctorCode domain state (projects, agent history, action feed) is modelled with
Pydantic and stored below the core-managed private app directory.

**Why:** `core_pack` is deliberately business-logic-free. Reimplementing its
infrastructure would violate v2.7 §0.8; putting product data into core would
violate the core's stated boundaries.

## D-003 — Local pairing is required by default

**Decision:** LAN exposure requires a six-digit pairing code shown on the PC.
Pairing returns a bearer token, persisted on the phone. mDNS removes the need to
type an IP address, not the need to authorise a new device.

**Why:** a local WebSocket that can run shell commands must not be open to every
device on a shared Wi-Fi network. The pairing check can be disabled explicitly
in Settings -> Mobile, with a warning.

## D-004 — API keys stay on the PC

**Decision:** API keys are stored only in the local, permission-restricted
settings file below the app data directory. They are never sent to the Android
companion; Android talks only to the DoctorCode server.

**Consequence:** current builds do not provide OS-keychain storage. This is
recorded as a known limitation, not silently hidden.
