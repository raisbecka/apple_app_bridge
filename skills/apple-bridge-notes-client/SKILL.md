---
name: apple-bridge-notes-client
description: "Drive Apple Notes via the bridge REST client."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Notes, Apple, macOS, REST, docker, bridge]
    related_skills: [apple-notes]
---

# Apple Bridge Notes Client

CLI client for the `apple_app_bridge` server. It talks HTTP/JSON to
`bridge_server.py` running on the host Mac, which drives the native
**Apple Notes** app (Notes.app, the iOS Notes app — the same iCloud-synced
store). The client itself is plain stdlib Python and runs anywhere,
including inside a Docker container on the host.

Use this when a container (or any non-macOS process) needs to read or
write Apple Notes. Use the `apple-notes` skill (memo CLI) when running
directly on the Mac.

## When to Use

- A Docker container on this machine needs Apple Notes access
- Scripting notes CRUD over HTTP instead of AppleScript
- Don't use for: on-Mac note management (use the `apple-notes` skill),
  Obsidian vaults, or agent-internal notes (use the `memory` tool)

## Prerequisites

- `bridge_server.py` running on the host (it must run on macOS — it drives
  Notes.app). Default endpoint: `127.0.0.1:6002`.
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
python3 notes_client.py --host 192.168.65.2 --port 6002 --api-key KEY <subcommand>
```

Server-side modes affect responses: a read-only server (`--read-only`)
rejects `create`/`delete` with 403; a server started with `--no-notes`
returns 503 on every notes endpoint.

## Operations

| Subcommand | Operation | Parameters |
|---|---|---|
| `list` | List all notes (title, folder, id) | `--folder F` filter by folder name |
| `get <id>` | Full note incl. HTML body | id from `list` (`x-coredata://.../ICNote/pNN`) |
| `create <title>` | Create a note | `--folder F` (default `Notes`), `--content FILE` or `--content -` for stdin body |
| `delete <id>` | Delete a note (server verifies + retries) | — |
| `status` | Server mode, module state, endpoint list | — |

Exit codes: `0` success, `1` API error (JSON error printed to stdout),
`2` bridge unreachable.

## Quick Reference

```bash
export APPLE_BRIDGE_HOST=192.168.65.2   # from a container
export APPLE_BRIDGE_PORT=6002
export APPLE_BRIDGE_API_KEY=***            # only if server uses --api-key

python3 notes_client.py status
python3 notes_client.py list --folder "Notes"
python3 notes_client.py get x-coredata://.../ICNote/p42
printf 'body text' | python3 notes_client.py create "My note" --folder Notes --content -
python3 notes_client.py delete x-coredata://.../ICNote/p42
```

## Pitfalls

- `127.0.0.1` inside a container is the container itself — always pass the
  host's bridge IP (or `host.docker.internal` with the matching
  `--add-host` flag on Linux).
- Note ids are long `x-coredata://...` URIs; always take them from
  `list` output, never hand-type them.
- A freshly created note can take a moment to appear in `list`
  (iCloud/Core Data lag); the server's delete endpoint already
  verify-and-retries, so trust its `deleted: true`.
- Do not publish the bridge port to the LAN (`-p 6002:6002` would expose
  Apple Notes beyond the host).

## Verification

`python3 notes_client.py status` returns the service JSON with
`"service": "apple_app_bridge"` and the expected `mode`/`modules` flags.
A create → get → delete cycle that ends with `get` returning a 404 JSON
error proves the full path works.
