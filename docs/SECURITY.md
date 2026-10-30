# Security and network endpoints

DoctorCode controls a real shell and real files. Its security model is
deliberately narrow: the desktop is trusted, the phone is a paired remote
control, and the network is assumed to be a private LAN, not the public
Internet.

## 1. Assets

| Asset | Where it lives | Why it matters |
|---|---|---|
| Provider API keys | Desktop `config/settings.json` | Billable credentials for an external LLM endpoint |
| Pairing bearer tokens | Phone Keystore; desktop `server_tokens.json` (hash only) | Grants control of prompts, STOP, and project opening |
| Project source code | Desktop project folders | Read/written by agent tools |
| Session history and imported snapshots | Desktop app-data folder | Private prompts, tool output, attachments |
| Action feed | Desktop memory + `sessions/*.json` | Can contain command output and file contents |

The Android app never receives provider API keys, settings files, project
folders, or private key material. It receives the folder path of an open project
(for display and opening another project), project state, and action-feed
events.

## 2. Trust boundaries

```
Phone (untrusted until paired)
   │  HTTP + WebSocket, bearer token, private LAN only
   ▼
FastAPI bridge (0.0.0.0:<configured port>)
   │  in-process calls
   ▼
Agent engine (trusted desktop process)
   ├── tools: filesystem / commands / tests
   └── OpenAI-compatible endpoint configured by the user
```

- The desktop is trusted. It runs the agent, stores keys, and executes tools.
- The phone is untrusted before pairing and semi-trusted after pairing. It can
  control existing tabs and open new project folders, but cannot read API keys.
- The LAN is untrusted. Anyone on the same Wi-Fi can attempt to reach the port.
- The model endpoint is external and receives only the conversation the agent
  sends. DoctorCode does not send settings, keys, or other projects to it.

## 3. Pairing and authentication

### Pairing code

- Six digits, generated with `secrets.randbelow`.
- Rotatable without revoking already paired devices.
- Compared in constant time (`hmac.compare_digest`).
- Five failed attempts within five minutes lock pairing for 60 seconds.
- At most eight paired devices are kept; adding a ninth drops the oldest.
- Pairing is enabled by default. Disabling it is an explicit Settings choice and
  produces a visible warning; the server then accepts unauthenticated LAN
  clients.

### Bearer tokens

- 32 random bytes, URL-safe encoded (`secrets.token_urlsafe(32)`).
- Only SHA-256 hashes are persisted in `server_tokens.json`.
- Sent by Android in the `Authorization: Bearer <token>` header.
- The server also accepts `?token=` on `/ws` for compatibility with simple
  clients, but the shipped Android client never puts a token in a URL. URLs are
  more likely to appear in proxy logs, screenshots, and crash reports.
- Revoking all devices clears the hash file; previously issued tokens stop
  working immediately.

### Authentication coverage

Every control route requires a bearer token when pairing is enabled:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/health` | none | Version, pairing flag, open-tab count |
| `POST` | `/api/pair` | pairing code | Exchange a code for a bearer token |
| `GET` | `/api/settings` | bearer | Sanitized settings, pairing status, discovery status |
| `GET` | `/api/projects` | bearer | List open project tabs |
| `POST` | `/api/projects` | bearer | Open a folder as a project tab |
| `GET` | `/api/projects/{id}` | bearer | One project's public state |
| `GET` | `/api/projects/{id}/events?limit=200` | bearer | Recent action-feed events |
| `POST` | `/api/projects/{id}/prompt` | bearer | Queue or wait for a prompt |
| `POST` | `/api/projects/{id}/stop` | bearer | Cancel the active run |
| `POST` | `/api/projects/{id}/auto` | bearer | Toggle No-Watch |
| `POST` | `/api/projects/{id}/model` | bearer | Select a model for the tab |
| `POST` | `/api/projects/{id}/review` | bearer | Run the read-only reviewer |
| `WS` | `/ws` | bearer | `hello`, `snapshot`, `event`, `ack`, `error`, `pong` |

`/api/health` intentionally reveals the app name, version, pairing requirement,
and number of open tabs. It does not reveal project paths, model names, keys, or
tokens.

### Protocol authorization details

- WebSocket close code `4401` means the token was rejected.
- REST `401` means the token is missing/revoked; double-check that HTTP status
  and expire the phone's saved token.
- REST `409` means the tab is busy. This is a state conflict, not an auth
  failure.
- REST `429` on `/api/pair` means the lockout is active; honour `Retry-After`.
- `sanitized()` settings strip API keys before they cross the bridge. The
  Android client displays only base URL and model.

## 4. Android hardening

- **Private-host policy.** `NetworkPolicy` accepts only loopback, RFC1918,
  link-local, CGNAT, IPv6 ULA/link-local, `localhost`, and `.local` hosts.
  Public IPs and arbitrary DNS names are rejected before OkHttp is called.
- **No credentials in URLs.** The bearer token is added as a request header.
- **Keystore encryption.** The token is encrypted with an AES-256/GCM key in
  `AndroidKeyStore`; ciphertext and IV are stored in app-private preferences.
  The key is non-exportable.
- **No backup.** `android:allowBackup="false"`, `fullBackupContent="false"`, and
  data-extraction rules exclude all shared preferences from cloud backup and
  device transfer.
- **No cleartext to the public Internet.** The app allows cleartext because the
  desktop bridge is plain HTTP on the LAN, but `NetworkPolicy` restricts where
  that cleartext may go.
- **Minimal permissions.** `INTERNET`, network state, Wi-Fi multicast (mDNS),
  and `RECORD_AUDIO` (optional voice input). No storage, contacts, location, or
  background service permissions.
- **No background service.** Discovery and sockets stop when the activity is
  cleared; the app does not run a persistent agent on the phone.
- **Strict parsing.** Unknown WebSocket frame types are ignored, malformed
  frames are dropped, and API models use `ignoreUnknownKeys` so a newer desktop
  cannot crash an older phone.

## 5. Desktop hardening

- App data is created through `core_pack.ensure_private_dir` and written
  atomically. On Windows the per-user `%LOCALAPPDATA%` ACL is the protection
  boundary; POSIX builds apply `0600` to the settings file.
- `SettingsStore.save()` writes a validated copy; malformed request bodies are
  rejected by Pydantic (`extra="forbid"`).
- The server binds `0.0.0.0` only when the user explicitly starts it. The GUI
  does not silently expose the LAN.
- Prompt length is capped at 200,000 characters; event payloads are bounded by
  the feed and file-size settings.
- Destructive commands, tests, writes, and package actions ask for approval
  unless the user enables No-Watch. The reviewer is read-only by construction.
- Model-catalogue errors are typed `LLMError` values with a `MODELS_*` code;
  provider exception text is never allowed to include the key (regression
  R-008).

## 6. Local data and logging

- `config/settings.json` contains API keys. It is never copied into imports,
  handoffs, feed events, Android payloads, or logs.
- `imports/kimi/*.json` are verbatim `doctorcode.session` v1 snapshots. They can
  contain the original conversation and tool output, but not DoctorCode
  settings or provider keys (regression R-005).
- `sessions/*.json` contain message history and feed events; treat them as
  sensitive as the project itself.
- The rotating log records lifecycle and error metadata. Do not paste it into a
  public issue without reviewing command output.
- Chat prompts and tool results are sent to the model endpoint chosen by the
  user. Review that provider's retention policy before sending proprietary code.

## 7. mDNS

The desktop advertises:

```
_doctorcode._tcp.local.
  version  = 0.1.0
  pairing  = required | disabled
  path     = /api
```

The instance name is the sanitized hostname. Addresses are local IPv4
addresses. No pairing code, token, API key, model name, project path, or device
name is published. mDNS is best-effort: if it fails, the server stays up and the
user can type the address manually.

## 8. Known limitations

These are recorded rather than hidden:

1. **No TLS.** The LAN bridge is cleartext HTTP/WebSocket. A passive attacker on
   the same network can read prompts and feed events. Use a trusted network or a
   VPN. TLS with a self-signed certificate and pinning is future work.
2. **Desktop API keys are not in an OS keychain.** They live in the private
   settings file protected by filesystem permissions only (D-004).
3. **Pairing can be disabled.** This is useful for isolated lab networks but
   removes authentication entirely; the UI warns about it.
4. **Opening projects is remote control.** A paired phone can point the agent at
   any folder the desktop user can access. Pair only devices you control.
5. **The phone is not a sandbox for the agent.** It renders tool output,
   including command stderr and file contents, in the feed.
6. **Release APKs are not signed for distribution.** Build a signed release
   before installing outside a development machine.

## 9. Reporting a vulnerability

Open a private security advisory on
`https://github.com/nuovogoat/doctorcode/security/advisories` rather than a
public issue. Include the version, platform, reproduction steps, and whether the
desktop server was reachable from an untrusted network. Never include real API
keys or private source code.
