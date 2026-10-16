---
name: apple-bridge-reminders-client
description: "Drive Apple Reminders via the bridge REST client."
version: 1.0.0
author: Andrew Raisbeck, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Reminders, Apple, macOS, REST, docker, bridge]
    related_skills: [apple-reminders]
---

# Apple Bridge Reminders Client

CLI client for the `apple_app_bridge` server. It talks HTTP/JSON to
`bridge_server.py` running on the host Mac, which drives the native
**Apple Reminders** app (Reminders.app, the iOS Reminders app — the same
iCloud-synced store) via `remindctl`. The client itself is plain stdlib
Python and runs anywhere, including inside a Docker container on the host.

Use this when a container (or any non-macOS process) needs to read or
write Apple Reminders. Use the `apple-reminders` skill (remindctl) when
running directly on the Mac.

## When to Use

- A Docker container on this machine needs Apple Reminders access
- Creating to-dos with due dates that sync to the user's iPhone/iPad
  from a non-macOS process
- Don't use for: on-Mac reminder management (use the `apple-reminders`
  skill), agent-scheduled alerts (use the cronjob tool), or calendar
  events

## Prerequisites

- `bridge_server.py` running on the host (it must run on macOS — it
  drives Reminders.app via remindctl). Default endpoint: `127.0.0.1:6002`.
- From a Docker container, target the host's Docker bridge gateway IP
  (commonly `192.168.65.2` on Docker Desktop for Mac), not `127.0.0.1`
  (that is the container's own loopback).
- If the server was started with `--api-key`, the key must be supplied
  (flag or env) or every request returns 401.

## Runtime Parameters

Resolution order for each, first match wins:

| Parameter | CLI flag | Env var | Default |
|---|---|---|---|
| Host | `--host` | `APPLE_BRIDGE_HOST` | `127.0.0.1` |
| Port | `--port` | `APPLE_BRIDGE_PORT` | `6002` |
| API key | `--api-key` | `APPLE_BRIDGE_API_KEY` | none |

All three are global flags and must appear **before** the subcommand:

```bash
python3 reminders_client.py --host 192.168.65.2 --port 6002 --api-key KEY <subcommand>
```

Server-side modes affect responses: a read-only server (`--read-only`)
rejects `create`/`complete`/`delete` with 403; a server started with
`--no-reminders` returns 503 on every reminders endpoint.

## Operations

| Subcommand | Operation | Parameters |
|---|---|---|
| `list [filter]` | List reminders | filter: `today` \| `tomorrow` \| `week` \| `overdue` \| `all` (default) \| `YYYY-MM-DD` |
| `lists` | List reminder lists (name, id, counts) | — |
| `get <id>` | One reminder (full JSON incl. `dueDate`, `alarmDate`) | UUID from `list` |
| `create <title>` | Create a reminder | `--list L` target list name, `--due D`, `--alarm A` |
| `complete <id>` | Mark a reminder completed | — |
| `delete <id>` | Delete a reminder | — |
| `status` | Server mode, module state, endpoint list | — |

Date formats accepted by `--due`, `--alarm`, and the `list` filter:
`today`, `tomorrow`, `yesterday`, `YYYY-MM-DD`, `YYYY-MM-DD HH:mm`, or
ISO 8601.

`--due` and `--alarm` are different fields: `--due` is the reminder's due
date; `--alarm` is the notification trigger (e.g. an earlier nudge).

Exit codes: `0` success, `1` API error (JSON error printed to stdout),
`2` bridge unreachable.

## Quick Reference

```bash
export APPLE_BRIDGE_HOST=192.168.65.2   # from a container
export APPLE_BRIDGE_PORT=6002
export APPLE_BRIDGE_API_KEY=***            # only if server uses --api-key

python3 reminders_client.py status
python3 reminders_client.py list today
python3 reminders_client.py lists
python3 reminders_client.py create "Call dentist" --due tomorrow
python3 reminders_client.py create "Hairdresser" --due "2026-05-15 14:00" --alarm "2026-05-15 13:30"
python3 reminders_client.py get <uuid>
python3 reminders_client.py complete <uuid>
python3 reminders_client.py delete <uuid>
```

## Pitfalls

- `127.0.0.1` inside a container is the container itself — always pass the
  host's bridge IP (or `host.docker.internal` with the matching
  `--add-host` flag on Linux).
- Reminder ids are UUIDs; take them from `list` output, never hand-type.
- The Reminders UI may group a timed reminder by its **alarm** time, not
  its due time. Verify with `get` (JSON `dueDate` vs `alarmDate`) rather
  than assuming the due time moved.
- `create` returns the new reminder's `id` when the server can resolve it
  (best-effort match on title); if absent, look it up via `list`.
- Do not publish the bridge port to the LAN (`-p 6002:6002` would expose
  Apple Reminders beyond the host).

## Verification

`python3 reminders_client.py status` returns the service JSON with
`"service": "apple_app_bridge"` and the expected `mode`/`modules` flags.
A create → get → complete → delete cycle that ends with `get` returning a
404 JSON error proves the full path works.
