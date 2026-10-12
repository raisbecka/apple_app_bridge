# apple_app_bridge

A small, stdlib-only Python REST bridge that exposes the host Mac's
**Apple Notes** (`memo` tool / AppleScript) and **Apple Reminders**
(`remindctl`) to Docker containers running on the same machine.

```
docker container ──HTTP──> 127.0.0.1:6002 (bridge_server.py) ──> memo / remindctl / osascript
```

## Files

| File | Purpose |
|---|---|
| `bridge_server.py` | The REST server (stdlib only, no deps) |
| `notes_client.py` | CLI client for the Notes API |
| `reminders_client.py` | CLI client for the Reminders API |

## Security model

- **Binds to `127.0.0.1` only** by default. Containers reach it via the
  Docker bridge gateway IP (find it with `ip route show default` inside the
  container, commonly `192.168.65.2` for Docker Desktop). It is never
  reachable from other machines on the LAN or the internet.
- **Optional shared secret**: start the server with `--api-key <key>` (or
  `APPLE_BRIDGE_API_KEY`). Every request then needs the header
  `X-Apple-Bridge-Key: <key>`.
- **Read-only mode**: `--read-only` makes all write endpoints return
  `403 read-only access`.
- **Module switches**: `--no-notes` / `--no-reminders` (or
  `APPLE_BRIDGE_NOTES=false` / `APPLE_BRIDGE_REMINDERS=false`) disable a
  whole module; its endpoints return `503`.

## Running the server

```bash
# minimal
python3 bridge_server.py

# typical: custom port, read-write, with a shared secret
python3 bridge_server.py --port 6002 --api-key "$(openssl rand -hex 16)"

# read-only, reminders disabled
python3 bridge_server.py --read-only --no-reminders
```

All flags also work as environment variables:

| Flag | Env var | Default |
|---|---|---|
| `--port N` | `APPLE_BRIDGE_PORT` | `6002` |
| `--host ADDR` | — | `127.0.0.1` |
| `--read-only` | `APPLE_BRIDGE_READ_ONLY=true` | off |
| `--no-notes` | `APPLE_BRIDGE_NOTES=false` | notes on |
| `--no-reminders` | `APPLE_BRIDGE_REMINDERS=false` | reminders on |
| `--api-key K` | `APPLE_BRIDGE_API_KEY` | off |

Backend binary locations are overridable via `APPLE_BRIDGE_MEMO` and
`APPLE_BRIDGE_REMINDCTL` (defaults: `~/.local/bin/memo`,
`~/.local/bin/remindctl`).

## API

Base URL: `http://<host>:<port>`

### Notes

| Method & path | Body / query | Description |
|---|---|---|
| `GET /notes/v1/list` | `?folder=Name` | List notes (optionally one folder). Returns `{count, notes:[{folder,title,id}]}` |
| `GET /notes/v1/get` | `?id=x-coredata://.../ICNote/pNN` | Full note incl. HTML body |
| `POST /notes/v1/create` | `{"title", "content_md", "folder"}` | Create note (`folder` defaults to `Notes`) |
| `POST /notes/v1/delete` | `{"id"}` | Delete note |

### Reminders

| Method & path | Body / query | Description |
|---|---|---|
| `GET /reminders/v1/list` | `?filter=today\|tomorrow\|week\|overdue\|all\|YYYY-MM-DD` | List reminders |
| `GET /reminders/v1/lists` | — | List reminder lists |
| `GET /reminders/v1/get` | `?id=<uuid>` | One reminder |
| `POST /reminders/v1/create` | `{"title", "list"?, "due"?, "alarm"?}` | Create reminder |
| `POST /reminders/v1/complete` | `{"id"}` | Mark complete |
| `POST /reminders/v1/delete` | `{"id"}` | Delete |

`due` / `alarm` accept `today`, `tomorrow`, `yesterday`, `YYYY-MM-DD`,
`YYYY-MM-DD HH:mm`, or ISO 8601.

### Meta

`GET /` — service name, mode, module state, endpoint list.

### Errors

All errors are JSON: `{"error": "...", "detail": "..."}` with the
appropriate HTTP status (400 bad input, 401 bad key, 403 read-only,
404 not found, 405 bad method, 503 module disabled, 500 backend failure,
504 backend timeout).

## Clients

Both clients take the endpoint from CLI flags, then env vars, then
defaults:

```
--host / APPLE_BRIDGE_HOST   (default 127.0.0.1)
--port / APPLE_BRIDGE_PORT   (default 6002)
--api-key / APPLE_BRIDGE_API_KEY
```

```bash
# from the host
python3 notes_client.py list
python3 notes_client.py create "Groceries" --folder "Notes" --content -
python3 reminders_client.py list today
python3 reminders_client.py create "Call dentist" --due tomorrow

# from a Docker container (bridge gateway IP as host)
docker run --rm -it python:3.12-slim \
  python /work/notes_client.py --host 192.168.65.2 --port 6002 list
```

Exit codes: `0` success, `1` API error (JSON printed to stdout),
`2` bridge unreachable.

## Docker usage notes

- The server must be running on the **host** (it drives AppleScript /
  the native apps, so it cannot itself run in a container).
- From a container on the default `bridge` network, the host is the
  gateway IP (`192.168.65.2` on Docker Desktop for Mac). On Linux with
  the default bridge, `host.docker.internal` also works if the
  `--add-host=host.docker.internal:host-gateway` flag is set.
- Keep the server on loopback; do **not** publish port 6002 with
  `-p` — that would expose Apple Notes/Reminders to the LAN.

## Prerequisites

- macOS with Notes.app and Reminders.app
- `memo` on the host (`~/.local/bin/memo`)
- `remindctl` on the host (`~/.local/bin/remindctl`) with Reminders
  access granted (`remindctl status`)
- Python 3.9+ (stdlib only — no pip installs needed)
