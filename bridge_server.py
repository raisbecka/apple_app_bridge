#!/usr/bin/env python3
"""apple_app_bridge server.

Wraps the host's `memo` (Apple Notes) and `remindctl` (Apple Reminders)
tools behind a small REST API so Docker containers on this machine can
drive the native apps.

Security model:
  - Binds to 127.0.0.1 ONLY. Reachable from containers via the host's
    Docker bridge IP (e.g. 192.168.65.2), never exposed on the LAN.
  - Optional shared secret: --api-key / APPLE_BRIDGE_API_KEY. When set,
    every request must carry `X-Apple-Bridge-Key: <key>`.
  - --read-only: write endpoints (create/complete/delete) return 403.
  - --no-notes / --no-reminders: disable a whole module (endpoints 503).

Stdlib only. No third-party dependencies.

Endpoints
  GET  /
  GET  /notes/v1/list[?folder=F]
  GET  /notes/v1/get?id=<note-id>
  POST /notes/v1/create            {"title": str, "content_md": str, "folder": str}
  POST /notes/v1/delete            {"id": str}
  GET  /reminders/v1/list[?filter=today|tomorrow|week|overdue|all|YYYY-MM-DD]
  GET  /reminders/v1/lists
  GET  /reminders/v1/get?id=<uuid>
  POST /reminders/v1/create        {"title": str, "list": str, "due": str, "alarm": str}
  POST /reminders/v1/complete      {"id": str}
  POST /reminders/v1/delete        {"id": str}
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 6002
MEMO_BIN = os.environ.get("APPLE_BRIDGE_MEMO", os.path.expanduser("~/.local/bin/memo"))
REMINDCTL_BIN = os.environ.get("APPLE_BRIDGE_REMINDCTL", os.path.expanduser("~/.local/bin/remindctl"))
SUBPROC_TIMEOUT = 60

CONFIG = {
    "api_key": None,
    "read_only": False,
    "notes_enabled": True,
    "reminders_enabled": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    """Expected failure -> JSON error response with a status code."""

    def __init__(self, status, message, detail=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def run_cmd(argv, timeout=SUBPROC_TIMEOUT):
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise BridgeError(500, f"backend binary not found: {argv[0]}",
                          "set APPLE_BRIDGE_MEMO / APPLE_BRIDGE_REMINDCTL")
    except subprocess.TimeoutExpired:
        raise BridgeError(504, f"backend timed out after {timeout}s: {argv[0]}")
    return proc.returncode, proc.stdout, proc.stderr


def run_json(argv, what):
    rc, out, err = run_cmd(argv)
    if rc != 0:
        raise BridgeError(500, f"{what} failed (exit {rc})", (err or out).strip()[:2000])
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise BridgeError(500, f"{what} returned invalid JSON", str(exc))


# ---------------------------------------------------------------------------
# Notes backend (AppleScript via osascript)
# ---------------------------------------------------------------------------

def _esc(s):
    """Escape a string for embedding in an AppleScript double-quoted literal.

    AppleScript string literals cannot contain raw newlines, so each newline
    is replaced with the concatenation `& linefeed &`. The caller must wrap
    the result so the leading/trailing `&` are absorbed (see notes_create).
    """
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", '" & linefeed & "')
    return out


def _osascript(script, retries=2):
    """Run an AppleScript, retrying on transient -1700 (can't communicate) errors."""
    last_err = ""
    for attempt in range(retries + 1):
        rc, out, err = run_cmd(["osascript", "-e", script])
        stripped = out.strip()
        if rc == 0 and not re.fullmatch(r"ERR#-?1700", stripped):
            return out
        last_err = (err or out).strip()
        if rc == 0 and stripped.startswith("ERR#") and stripped != "ERR#-1700":
            return out  # definitive error (e.g. -1728), don't retry
        time.sleep(1.5 * (attempt + 1))
    raise BridgeError(500, "Notes.app scripting failed", last_err[:2000] or "transient -1700")


NOTES_LIST_SCRIPT = r'''
set recEnd to linefeed & "#@#"
set out to ""
tell application "Notes"
    repeat with f in folders
        repeat with n in notes of f
            set out to out & (name of f) & linefeed & (name of n) & recEnd & (id of n) & recEnd
        end repeat
    end repeat
end tell
return out
'''

NOTES_GET_SCRIPT = r'''
try
    set found to ""
    tell application "Notes"
        repeat with f in folders
            repeat with n in notes of f
                if (id of n) is "%ID%" then
                    set found to (name of n) & linefeed & (name of f) & linefeed & (creation date of n) & linefeed & (modification date of n) & linefeed & (body of n)
                end if
            end repeat
        end repeat
    end tell
    if found is "" then return "NOTFOUND"
    return found
on error number m
    return "ERR#" & m
end try
'''

NOTES_DELETE_SCRIPT = r'''
try
    tell application "Notes"
        delete (first note whose id is "%ID%")
    end tell
    return "deleted"
on error number m
    return "ERR#" & m
end try
'''


def notes_list(folder=None):
    raw = _osascript(NOTES_LIST_SCRIPT)
    segments = raw.split("#@#")
    notes = []
    # Each record occupies two segments: "folder\ntitle" then "id".
    for i in range(0, len(segments) - 1, 2):
        head, note_id = segments[i], segments[i + 1].strip()
        if not note_id:
            continue
        lines = head.split("\n", 1)
        folder_name = lines[0]
        title = lines[1] if len(lines) > 1 else ""
        if folder is None or folder_name == folder:
            notes.append({"folder": folder_name, "title": title, "id": note_id})
    return {"count": len(notes), "notes": notes}


def notes_get(note_id):
    if not re.fullmatch(r"x-coredata://[^\s/]+/ICNote/p\d+", note_id):
        raise BridgeError(400, "invalid note id", "expected x-coredata://.../ICNote/pNN")
    out = _osascript(NOTES_GET_SCRIPT.replace("%ID%", note_id)).rstrip("\n")
    if out == "NOTFOUND":
        raise BridgeError(404, "note not found", note_id)
    if re.fullmatch(r"ERR#-?\d+", out):
        raise BridgeError(500, "Notes.app error", out)
    parts = out.split("\n", 4)
    if len(parts) < 5:
        raise BridgeError(500, "unexpected Notes.app response shape")
    return {"id": note_id, "title": parts[0], "folder": parts[1],
            "creation_date": parts[2], "modification_date": parts[3],
            "body_html": parts[4]}


def notes_create(title, content_md, folder):
    if not title.strip():
        raise BridgeError(400, "title must be non-empty")
    if folder is None:
        folder = "Notes"
    body_html = (f'<html><head></head><body class="Apple">'
                 f'<h1>{title}</h1>\n{content_md}</body></html>')
    escaped_body = _esc(body_html)
    script = (
        'try\n'
        '    tell application "Notes"\n'
        f'        set targetFolder to first folder whose name is "{_esc(folder)}"\n'
        '        tell targetFolder\n'
        f'            set newNote to make new note with properties {{body:"{escaped_body}"}}\n'
        '        end tell\n'
        '        return (id of newNote)\n'
        '    end tell\n'
        'on error number m\n'
        '    return "ERR#" & m\n'
        'end try'
    )
    out = _osascript(script).strip()
    if re.fullmatch(r"ERR#-?\d+", out):
        raise BridgeError(404, f"folder '{folder}' does not exist in Notes.app",
                          "use /notes/v1/list to see available folders")
    return {"id": out, "title": title, "folder": folder, "created": True}


def _note_exists(note_id):
    """Return True if a note with this id is still present in Notes.app."""
    script = (
        'tell application "Notes"\n'
        'set c to 0\n'
        'repeat with f in folders\n'
        'repeat with n in notes of f\n'
        f'if (id of n) is "{note_id}" then set c to c + 1\n'
        'end repeat\n'
        'end repeat\n'
        'return c\n'
        'end tell'
    )
    try:
        out = _osascript(script).strip()
    except BridgeError:
        return True  # can't confirm absence -> assume it may still be there
    try:
        return int(out) > 0
    except ValueError:
        return True


def notes_delete(note_id):
    if not re.fullmatch(r"x-coredata://[^\s/]+/ICNote/p\d+", note_id):
        raise BridgeError(400, "invalid note id", "expected x-coredata://.../ICNote/pNN")
    # Notes.app + iCloud can lag on committing a delete (a freshly created note
    # in particular). Issue the delete, then verify it stuck, retrying a few
    # times with a short settle delay before giving up.
    for attempt in range(3):
        out = _osascript(NOTES_DELETE_SCRIPT.replace("%ID%", note_id)).strip()
        if out == "NOTFOUND" or re.fullmatch(r"ERR#-?\d+", out):
            # Definitively not present (or a hard error) -> treat as gone.
            if not _note_exists(note_id):
                return {"id": note_id, "deleted": True}
            raise BridgeError(404, "note not found", note_id)
        time.sleep(1.0)
        if not _note_exists(note_id):
            return {"id": note_id, "deleted": True}
    raise BridgeError(500, "delete issued but note is still present",
                      "Notes.app/iCloud may be lagging; retry shortly")


# ---------------------------------------------------------------------------
# Reminders backend (remindctl)
# ---------------------------------------------------------------------------

REMINDER_FILTERS = {"today", "tomorrow", "week", "overdue", "all"}


def reminders_list(rem_filter=None):
    rem_filter = rem_filter or "all"
    if rem_filter in REMINDER_FILTERS:
        data = run_json([REMINDCTL_BIN, rem_filter, "--json"], "remindctl list")
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", rem_filter):
        data = run_json([REMINDCTL_BIN, rem_filter, "--json"], "remindctl list")
    else:
        raise BridgeError(400, "invalid filter",
                          f"expected one of {sorted(REMINDER_FILTERS)} or YYYY-MM-DD")
    return {"count": len(data), "reminders": data}


def reminders_lists():
    return {"lists": run_json([REMINDCTL_BIN, "lists", "--json"], "remindctl lists")}


def reminders_get(reminder_id):
    for r in run_json([REMINDCTL_BIN, "all", "--json"], "remindctl all"):
        if r.get("id") == reminder_id:
            return r
    raise BridgeError(404, "reminder not found", reminder_id)


def reminders_create(title, list_name=None, due=None, alarm=None):
    if not title.strip():
        raise BridgeError(400, "title must be non-empty")
    argv = [REMINDCTL_BIN, "add", "--title", title]
    if list_name:
        argv += ["--list", list_name]
    if due:
        argv += ["--due", due]
    if alarm:
        argv += ["--alarm", alarm]
    rc, out, err = run_cmd(argv)
    if rc != 0:
        raise BridgeError(500, "remindctl add failed", (err or out).strip()[:2000])
    created = {"title": title, "list": list_name, "due": due, "alarm": alarm,
               "created": True, "stdout": out.strip()}
    # Best-effort: resolve the new reminder's id.
    try:
        for r in run_json([REMINDCTL_BIN, "all", "--json"], "remindctl all"):
            if r.get("title") == title:
                created["id"] = r["id"]
                break
    except BridgeError:
        pass
    return created


def reminders_complete(reminder_id):
    rc, out, err = run_cmd([REMINDCTL_BIN, "complete", reminder_id])
    if rc != 0:
        raise BridgeError(500, "remindctl complete failed", (err or out).strip()[:2000])
    return {"id": reminder_id, "completed": True}


def reminders_delete(reminder_id):
    rc, out, err = run_cmd([REMINDCTL_BIN, "delete", reminder_id, "--force"])
    if rc != 0:
        raise BridgeError(500, "remindctl delete failed", (err or out).strip()[:2000])
    return {"id": reminder_id, "deleted": True}


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "AppleAppBridge/1.0"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    # -- response helpers ---------------------------------------------------

    def _send(self, status, payload):
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message, detail=None):
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        self._send(status, payload)

    def _check_auth(self):
        if CONFIG["api_key"] and \
                self.headers.get("X-Apple-Bridge-Key") != CONFIG["api_key"]:
            self._error(401, "missing or invalid X-Apple-Bridge-Key header")
            return False
        return True

    def _require_module(self, module):
        if module == "notes" and not CONFIG["notes_enabled"]:
            self._error(503, "notes module is disabled")
            return False
        if module == "reminders" and not CONFIG["reminders_enabled"]:
            self._error(503, "reminders module is disabled")
            return False
        return True

    def _require_write(self):
        if CONFIG["read_only"]:
            self._error(403, "read-only access: this endpoint requires read-write mode")
            return False
        return True

    def _body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        if length > 1_000_000:
            raise BridgeError(413, "request body too large (max 1MB)")
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BridgeError(400, "invalid JSON body", str(exc))
        if not isinstance(data, dict):
            raise BridgeError(400, "JSON body must be an object")
        return data

    # -- routing ------------------------------------------------------------

    def _route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if not self._check_auth():
            return

        try:
            self._dispatch(method, path, query)
        except BridgeError as exc:
            self._error(exc.status, exc.message, exc.detail)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            self._error(500, "internal server error", str(exc)[:500])

    def _dispatch(self, method, path, query):
        if path == "/":
            self._send(200, {
                "service": "apple_app_bridge",
                "version": "1.0.0",
                "mode": "read-only" if CONFIG["read_only"] else "read-write",
                "modules": {
                    "notes": CONFIG["notes_enabled"],
                    "reminders": CONFIG["reminders_enabled"],
                },
                "endpoints": [
                    "GET  /notes/v1/list[?folder=F]",
                    "GET  /notes/v1/get?id=<note-id>",
                    "POST /notes/v1/create",
                    "POST /notes/v1/delete",
                    "GET  /reminders/v1/list[?filter=today|tomorrow|week|overdue|all|YYYY-MM-DD]",
                    "GET  /reminders/v1/lists",
                    "GET  /reminders/v1/get?id=<uuid>",
                    "POST /reminders/v1/create",
                    "POST /reminders/v1/complete",
                    "POST /reminders/v1/delete",
                ],
            })
            return

        parts = [p for p in path.split("/") if p]
        if len(parts) != 3 or parts[1] != "v1":
            self._error(404, "unknown endpoint", path)
            return
        module, action = parts[0], parts[2]

        if not self._require_module(module):
            return

        # notes -------------------------------------------------------------
        if module == "notes":
            if method == "GET" and action == "list":
                self._send(200, notes_list(query.get("folder")))
            elif method == "GET" and action == "get":
                if not query.get("id"):
                    raise BridgeError(400, "missing ?id=<note-id>")
                self._send(200, notes_get(query["id"]))
            elif method == "POST" and action == "create":
                if not self._require_write():
                    return
                body = self._body_json()
                self._send(201, notes_create(
                    body.get("title", ""), body.get("content_md", ""), body.get("folder")))
            elif method == "POST" and action == "delete":
                if not self._require_write():
                    return
                body = self._body_json()
                if not body.get("id"):
                    raise BridgeError(400, "missing 'id'")
                self._send(200, notes_delete(body["id"]))
            else:
                self._error(404, "unknown endpoint", path)
            return

        # reminders ---------------------------------------------------------
        if module == "reminders":
            if method == "GET" and action == "list":
                self._send(200, reminders_list(query.get("filter")))
            elif method == "GET" and action == "lists":
                self._send(200, reminders_lists())
            elif method == "GET" and action == "get":
                if not query.get("id"):
                    raise BridgeError(400, "missing ?id=<uuid>")
                self._send(200, reminders_get(query["id"]))
            elif method == "POST" and action == "create":
                if not self._require_write():
                    return
                body = self._body_json()
                self._send(201, reminders_create(
                    body.get("title", ""), body.get("list"),
                    body.get("due"), body.get("alarm")))
            elif method == "POST" and action == "complete":
                if not self._require_write():
                    return
                body = self._body_json()
                if not body.get("id"):
                    raise BridgeError(400, "missing 'id'")
                self._send(200, reminders_complete(body["id"]))
            elif method == "POST" and action == "delete":
                if not self._require_write():
                    return
                body = self._body_json()
                if not body.get("id"):
                    raise BridgeError(400, "missing 'id'")
                self._send(200, reminders_delete(body["id"]))
            else:
                self._error(404, "unknown endpoint", path)
            return

        self._error(404, "unknown endpoint", path)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._error(405, "method not allowed", "use GET or POST")

    def do_DELETE(self):
        self._error(405, "method not allowed", "use POST /<module>/v1/delete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description="apple_app_bridge REST server")
    p.add_argument("--port", type=int,
                   default=int(os.environ.get("APPLE_BRIDGE_PORT", DEFAULT_PORT)),
                   help=f"listen port (default {DEFAULT_PORT}, env APPLE_BRIDGE_PORT)")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; keep loopback for safety)")
    p.add_argument("--read-only", action="store_true",
                   default=os.environ.get("APPLE_BRIDGE_READ_ONLY", "").lower() in ("1", "true", "yes"),
                   help="reject write endpoints with 403 (env APPLE_BRIDGE_READ_ONLY)")
    p.add_argument("--no-notes", action="store_true",
                   default=os.environ.get("APPLE_BRIDGE_NOTES", "true").lower() in ("0", "false", "no"),
                   help="disable the notes module (env APPLE_BRIDGE_NOTES=true|false)")
    p.add_argument("--no-reminders", action="store_true",
                   default=os.environ.get("APPLE_BRIDGE_REMINDERS", "true").lower() in ("0", "false", "no"),
                   help="disable the reminders module (env APPLE_BRIDGE_REMINDERS=true|false)")
    p.add_argument("--api-key", default=os.environ.get("APPLE_BRIDGE_API_KEY"),
                   help="require X-Apple-Bridge-Key header (env APPLE_BRIDGE_API_KEY)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    CONFIG["api_key"] = args.api_key
    CONFIG["read_only"] = args.read_only
    CONFIG["notes_enabled"] = not args.no_notes
    CONFIG["reminders_enabled"] = not args.no_reminders

    for name, path in (("memo", MEMO_BIN), ("remindctl", REMINDCTL_BIN)):
        if not os.path.exists(path):
            print(f"warning: {name} binary not found at {path}", file=sys.stderr)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"apple_app_bridge listening on http://{args.host}:{args.port} "
          f"[read_only={CONFIG['read_only']} notes={CONFIG['notes_enabled']} "
          f"reminders={CONFIG['reminders_enabled']} api_key={'on' if args.api_key else 'off'}]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
