# apple_app_bridge

A small, **stdlib-only Python REST bridge** that exposes your Mac's **Apple Notes** and **Apple Reminders** to Docker containers (or any process) running on the same machine.

```
┌─────────────────────┐        HTTP/JSON        ┌──────────────────────────────┐
│  Docker container   │ ─────────────────────► │  Mac host (127.0.0.1:6002)   │
│  notes_client.py    │                         │  bridge_server.py            │
│  reminders_client.py│ ◄─────────────────────  │        │                     │
└─────────────────────┘                         │        ▼                     │
                                                │  memo / remindctl / osascript│
                                                │        │                     │
                                                │        ▼                     │
                                                │  Notes.app · Reminders.app   │
                                                └──────────────────────────────┘
```

No pip installs, no frameworks — one server file and two thin CLI clients, all Python 3.9+ stdlib.

**Author:** Andrew Raisbeck

## Features

- 📝 **Apple Notes**: list, read, create, delete — including folder targeting and HTML bodies
- ✅ **Apple Reminders**: list (by date filter), read, create (due + alarm), complete, delete
- 🔒 **Loopback-only by default** — never reachable from your LAN
- 🔑 **Optional shared secret** (`X-Apple-Bridge-Key` header)
- 🧯 **Read-only mode** and per-module kill switches
- 🐳 **Docker-friendly**: clients work from any container on the host's bridge network
- 📡 Resilient AppleScript handling (retries on transient `-1700`, verify-and-retry deletes)

## Prerequisites

- macOS with Notes.app and Reminders.app
- [`memo`](https://github.com/steipete/memo) on the host (default: `~/.local/bin/memo`)
- `remindctl` on the host (default: `~/.local/bin/remindctl`) with Reminders access granted
- Python 3.9+ (stdlib only)

## Quick start

### 1. Run the server (on the Mac)

```bash
python3 bridge_server.py
```

Typical hardened setup:

```bash
python3 bridge_server.py --port 6002 --api-key "$(openssl rand -hex 16)"
```

### 2. Use the clients

```bash
# from the host
python3 notes_client.py list
python3 notes_client.py create "Groceries" --folder Notes --content -
python3 reminders_client.py list today
python3 reminders_client.py create "Call dentist" --due tomorrow
```

```bash
# from a Docker container — target the host's bridge gateway IP,
# NOT 127.0.0.1 (that's the container's own loopback)
python3 notes_client.py --host 192.168.65.2 --port 6002 list
```

### Configuration

Every flag has an environment-variable twin; resolution order is **CLI flag → env var → default**:

| Server flag | Env var | Default |
|---|---|---|
| `--port N` | `APPLE_BRIDGE_PORT` | `6002` |
| `--host ADDR` | — | `127.0.0.1` |
| `--read-only` | `APPLE_BRIDGE_READ_ONLY=true` | off |
| `--no-notes` | `APPLE_BRIDGE_NOTES=false` | notes on |
| `--no-reminders` | `APPLE_BRIDGE_REMINDERS=false` | reminders on |
| `--api-key K` | `APPLE_BRIDGE_API_KEY` | off |

Client flags: `--host` / `--port` / `--api-key` (env: `APPLE_BRIDGE_HOST`, `APPLE_BRIDGE_PORT`, `APPLE_BRIDGE_API_KEY`).

Backend binary locations are overridable via `APPLE_BRIDGE_MEMO` and `APPLE_BRIDGE_REMINDCTL`.

## API reference

Base URL: `http://<host>:<port>`

### Notes

| Method & path | Body / query | Description |
|---|---|---|
| `GET /notes/v1/list` | `?folder=Name` | List notes → `{count, notes:[{folder,title,id}]}` |
| `GET /notes/v1/get` | `?id=x-coredata://.../ICNote/pNN` | Full note incl. HTML body |
| `POST /notes/v1/create` | `{"title", "content_md", "folder"?}` | Create note (`folder` defaults to `Notes`) |
| `POST /notes/v1/delete` | `{"id"}` | Delete note (server verifies and retries) |

### Reminders

| Method & path | Body / query | Description |
|---|---|---|
| `GET /reminders/v1/list` | `?filter=today\|tomorrow\|week\|overdue\|all\|YYYY-MM-DD` | List reminders |
| `GET /reminders/v1/lists` | — | List reminder lists |
| `GET /reminders/v1/get` | `?id=<uuid>` | One reminder |
| `POST /reminders/v1/create` | `{"title", "list"?, "due"?, "alarm"?}` | Create reminder |
| `POST /reminders/v1/complete` | `{"id"}` | Mark complete |
| `POST /reminders/v1/delete` | `{"id"}` | Delete |

`due` / `alarm` accept `today`, `tomorrow`, `yesterday`, `YYYY-MM-DD`, `YYYY-MM-DD HH:mm`, or ISO 8601.

### Meta & errors

- `GET /` — service name, mode, module state, endpoint list
- All errors are JSON: `{"error": "...", "detail": "..."}` with the appropriate status
  (400 bad input · 401 bad key · 403 read-only · 404 not found · 405 bad method ·
  413 body too large · 503 module disabled · 500 backend failure · 504 backend timeout)

Client exit codes: `0` success · `1` API error (JSON on stdout) · `2` bridge unreachable.

## Security model

- **Binds to `127.0.0.1` only** by default. Containers reach it via the Docker bridge
  gateway IP (find it with `ip route show default` inside the container; commonly
  `192.168.65.2` on Docker Desktop for Mac). Never exposed on the LAN or internet.
- **Optional shared secret**: with `--api-key`, every request must carry
  `X-Apple-Bridge-Key: <key>` or it gets a `401`.
- **Read-only mode**: `--read-only` makes all write endpoints return `403`.
- **Module switches**: `--no-notes` / `--no-reminders` disable a whole module (`503`).
- ⚠️ Do **not** publish port 6002 with `-p` — that would expose Apple Notes/Reminders to your LAN.

## Docker usage notes

- The server must run on the **host** — it drives AppleScript and the native apps, so it
  cannot itself run in a container.
- From a container on the default `bridge` network, the host is the gateway IP
  (`192.168.65.2` on Docker Desktop for Mac). On Linux, use
  `--add-host=host.docker.internal:host-gateway` and target `host.docker.internal`.

## Project layout

```
apple_app_bridge/
├── bridge_server.py       # REST server (stdlib only, no deps)
├── notes_client.py        # CLI client for the Notes API
├── reminders_client.py    # CLI client for the Reminders API
└── skills/                # agent skills for the two clients
```

## License

MIT
