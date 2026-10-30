# DoctorCode companion for Android

The companion is the remote control for a DoctorCode desktop. It discovers the
desktop on the local network, pairs with a six-digit code, streams the structured
action feed, sends prompts, toggles No-Watch, and sends STOP. All model calls,
file access, and provider credentials stay on the desktop.

- **Minimum Android version:** 15 (API 35)
- **Language/UI:** Kotlin 2.0.21, Jetpack Compose (BOM 2024.12.01), Material 3
- **Networking:** OkHttp 4.12 REST + WebSocket, Android NSD/mDNS discovery
- **Build tools:** AGP 8.7.3, Gradle 8.9, JDK 17

## Build

Prerequisites:

1. JDK 17 (the Gradle wrapper and AGP 8.7.3 are tested on 17).
2. Android SDK Platform 35 and Build-Tools 35.0.0.
3. `ANDROID_HOME` or `android/local.properties` pointing at the SDK:

   ```properties
   sdk.dir=C\:\\Users\\<name>\\AppData\\Local\\Android\\Sdk
   ```

Then, from the `android/` folder:

```powershell
.\gradlew.bat :app:assembleDebug
.\gradlew.bat :app:testDebugUnitTest
.\gradlew.bat :app:lintDebug
```

The debug APK is written to
`app/build/outputs/apk/debug/app-debug.apk`:

```powershell
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

The project also opens directly in Android Studio (Ladybug or newer). Do not
commit `local.properties`, `.gradle/`, or `app/build/`.

## First connection

1. On the desktop, open **Mobile access** and start the LAN server. A six-digit
   pairing code is shown.
2. Keep the phone and the desktop on the same Wi-Fi network. Guest networks and
   client isolation block mDNS and direct connections.
3. Open the Android app. It scans for `_doctorcode._tcp.` and lists the desktops
   it finds.
4. Tap a desktop to fill its address, enter the pairing code, and press
   **Connect**. If discovery is blocked, type the address manually, for example
   `192.168.1.20:8765`; the app adds `http://`.
5. On success the token is encrypted with a Keystore key and saved. The next
   launch reconnects automatically.
6. **Forget this desktop** deletes the saved token and address from the phone.

If pairing is disabled on the desktop, leave the code empty. This is intended
only for isolated lab networks; see [`../docs/SECURITY.md`](../docs/SECURITY.md).

## What the app can do

- List open project tabs and their state (model, busy, No-Watch, event count).
- Open a desktop folder as a project tab by typing its absolute path.
- Send a prompt to the selected tab; the request is rejected with a visible
  error if the tab is already busy.
- Send STOP immediately, independent of the prompt request.
- Toggle No-Watch on the selected tab.
- Stream every action-feed event and expand metadata and raw payload.
- Dictate a prompt with the platform speech recognizer; the text is still
  editable before sending.
- Reconnect the WebSocket with exponential backoff after a network drop.

The app is intentionally not a project browser: it has no filesystem access and
cannot read desktop files directly. It only asks the desktop to open a path.

## REST contract

Base path: `http://<desktop>:<port>/api`. JSON bodies use snake_case. Control
routes require `Authorization: Bearer <token>` when pairing is enabled.

| Method | Path | Body | Response |
|---|---|---|---|
| `GET` | `/health` | — | `status`, `app`, `version`, `pairing_required`, `projects` |
| `POST` | `/pair` | `code`, `device_name` | `ok`, `token`, `pairing_required`, `server`, `version` |
| `GET` | `/settings` | — | sanitized settings, pairing status, discovery status |
| `GET` | `/projects` | — | `projects[]`, `count` |
| `POST` | `/projects` | `path` | `project` |
| `GET` | `/projects/{id}` | — | `project` |
| `GET` | `/projects/{id}/events?limit=200` | — | `session_id`, `events[]`, `count` |
| `POST` | `/projects/{id}/prompt` | `prompt`, optional `auto`, `wait` | `accepted`, `completed`, `project` |
| `POST` | `/projects/{id}/stop` | — | `stopped`, `project` |
| `POST` | `/projects/{id}/auto` | `enabled` | `project` |
| `POST` | `/projects/{id}/model` | `model` | `project` |
| `POST` | `/projects/{id}/review` | `instructions`, `material` | reviewer result |

A prompt without `wait` returns HTTP `202` immediately; progress arrives as
WebSocket events. `409` means the tab is busy. `401` means the token was revoked
and the app clears it.

### Project state

```json
{
  "session_id": "…",
  "project_name": "doctor_code",
  "project_path": "C:\\Projects\\doctor_code",
  "model": "the-selected-model",
  "auto_mode": false,
  "busy": false,
  "event_count": 12,
  "writer_configured": true,
  "reviewer_configured": false
}
```

No API key or base URL is included in a project state.

### Action event

```json
{
  "event_id": "…",
  "timestamp": "2026-10-28T10:00:00+00:00",
  "session_id": "…",
  "action_type": "run_command",
  "status": "SUCCESS",
  "human_summary": "Tests passed",
  "metadata": {"exit_code": 0},
  "raw_payload": "pytest -q",
  "duration_ms": 1200
}
```

`status` is one of `SUCCESS`, `FAILURE`, `IN_PROGRESS`. The UI colours the status
label from this field only.

## WebSocket contract

Connect with `ws://<desktop>:<port>/ws` and the bearer token in the
`Authorization` header. The app does not put the token in the URL.

Server → phone:

| Type | Payload |
|---|---|
| `hello` | `app`, `version`, `pairing_required` |
| `snapshot` | `projects`, `events` keyed by session id, `pairing_required` |
| `event` | one action event |
| `ack` | `action`, `session_id`, optional `stopped`/`accepted` |
| `error` | `code`, `message` |
| `pong` | reply to `ping` |

Phone → server: `ping`, `list_projects`, `stop`, `set_auto`, `prompt`. The
shipped app sends actions over REST and uses the socket for streaming and
keepalive.

## Discovery

The desktop advertises `_doctorcode._tcp.local.` with TXT records `version`,
`pairing`, and `path=/api`. The app resolves the service and shows its IPv4 or
IPv6 address. mDNS never carries a pairing code or token.

Some routers block multicast. If no desktop appears, use the manual address
field with the address printed by the desktop's Mobile access window.

## Network policy

The app refuses any base URL that is not a private LAN host:

- IPv4: `10/8`, `172.16/12`, `192.168/16`, `127/8`, `169.254/16`, `100.64/10`;
- IPv6: loopback, link-local, unique-local (`fc00::/7`), IPv4-mapped private;
- names: `localhost` and `*.local` only.

Public IPs and arbitrary DNS names are rejected before a request is made. The
policy is enforced in code and covered by JVM tests; it is not only a manifest
comment.

## Permissions

| Permission | Why |
|---|---|
| `INTERNET` | REST and WebSocket to the desktop |
| `ACCESS_NETWORK_STATE` | Diagnose connectivity |
| `ACCESS_WIFI_STATE`, `CHANGE_WIFI_MULTICAST_STATE` | mDNS discovery |
| `RECORD_AUDIO` | Optional voice prompt, requested at first use |

No storage, location, contacts, or background-service permissions are
requested. The app has no analytics and no third-party SDK.

## Security notes

- The bearer token is encrypted with AES-256/GCM under a non-exportable
  `AndroidKeyStore` key. Only ciphertext and IV are stored in private
  preferences.
- `allowBackup=false` and data-extraction rules exclude preferences, so the
  token cannot be restored onto another device.
- The LAN bridge is cleartext HTTP/WebSocket. Use a trusted network; see
  [`../docs/SECURITY.md`](../docs/SECURITY.md) for the full model and known
  limitations.
- Provider API keys never reach the phone. `/api/settings` returns a sanitized
  view.
- Release builds must be signed separately; the repository ships debug builds
  only.

## Project layout

```
android/
├── settings.gradle.kts
├── build.gradle.kts
├── gradle.properties
├── gradlew / gradlew.bat
├── gradle/wrapper/
└── app/
    ├── build.gradle.kts
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── java/com/nuovogoat/doctorcode/
        │   │   ├── MainActivity.kt
        │   │   ├── data/
        │   │   └── ui/
        │   └── res/
        │       ├── values/strings.xml
        │       ├── values-ru/strings.xml
        │       └── values-de/strings.xml
        └── test/java/com/nuovogoat/doctorcode/data/
```

## Localization

The app ships English, Russian, and German string packs with identical keys and
format specifiers. `tools/check_gates.py` enforces parity, so add every new key
to all three `strings.xml` files. Brand names and technical hints may be
identical between languages; a Cyrillic Russian value copied into German is a
gate failure.

## Troubleshooting

| Symptom | Check |
|---|---|
| No desktops found | Same network, server started, multicast allowed; try manual address |
| `private_network_only` | The address is public or an unsupported hostname; use the LAN IP or `.local` name |
| `401` / pairing expired | Ask the desktop to show a new code; connect again |
| `409` on prompt | The tab is already running; wait or send STOP |
| Voice button does nothing | Install/enable a speech recognition service; grant microphone permission |
| Build fails on JDK | Use JDK 17 and Android SDK 35; set `JAVA_HOME` and `sdk.dir` |
