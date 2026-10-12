#!/usr/bin/env python3
"""Client for the apple_app_bridge Notes API.

Endpoint resolution order (first match wins):
  1. --host / --port CLI flags
  2. APPLE_BRIDGE_HOST / APPLE_BRIDGE_PORT environment variables
  3. Defaults: 127.0.0.1:6002

From a Docker container on the same host, use the Docker bridge gateway IP,
e.g. `docker run ... notes_client.py --host 192.168.65.2 --port 6002 list`.

If the server was started with --api-key, set APPLE_BRIDGE_API_KEY (or
--api-key) and the header is sent automatically.

Usage:
  notes_client.py list [--folder F]
  notes_client.py get <note-id>
  notes_client.py create <title> [--folder F] [--content FILE|-]
  notes_client.py delete <note-id>
  notes_client.py status
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_HOST = os.environ.get("APPLE_BRIDGE_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("APPLE_BRIDGE_PORT", "6002"))
API_KEY = os.environ.get("APPLE_BRIDGE_API_KEY")


def request(method, path, body=None, host=DEFAULT_HOST, port=DEFAULT_PORT, api_key=None):
    url = f"http://{host}:{port}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    key = api_key if api_key is not None else API_KEY
    if key:
        headers["X-Apple-Bridge-Key"] = key
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"error": str(exc)}
        return exc.code, payload
    except urllib.error.URLError as exc:
        print(f"error: cannot reach bridge at {url}: {exc.reason}", file=sys.stderr)
        sys.exit(2)


def cmd_list(args):
    path = "/notes/v1/list"
    if args.folder:
        path += f"?folder={urllib.parse.quote(args.folder)}"
    status, data = request("GET", path, host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_get(args):
    status, data = request("GET", f"/notes/v1/get?id={urllib.parse.quote(args.id)}",
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_create(args):
    content = ""
    if args.content == "-":
        content = sys.stdin.read()
    elif args.content:
        with open(args.content, "r", encoding="utf-8") as fh:
            content = fh.read()
    status, data = request("POST", "/notes/v1/create",
                           body={"title": args.title, "content_md": content,
                                 "folder": args.folder},
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_delete(args):
    status, data = request("POST", "/notes/v1/delete", body={"id": args.id},
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_status(args):
    status, data = request("GET", "/", host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"bridge host (default {DEFAULT_HOST}, env APPLE_BRIDGE_HOST)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"bridge port (default {DEFAULT_PORT}, env APPLE_BRIDGE_PORT)")
    parser.add_argument("--api-key", default=None,
                        help="API key (default: env APPLE_BRIDGE_API_KEY)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list notes")
    p.add_argument("--folder", help="filter by folder name")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", help="get one note by id")
    p.add_argument("id", help="note id (x-coredata://.../ICNote/pNN)")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("create", help="create a note")
    p.add_argument("title")
    p.add_argument("--folder", help="target folder (default: Notes)")
    p.add_argument("--content", help="body text file, or '-' for stdin")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("delete", help="delete a note by id")
    p.add_argument("id")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("status", help="show bridge status/endpoints")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
