#!/usr/bin/env python3
"""Client for the apple_app_bridge Reminders API.

Endpoint resolution order (first match wins):
  1. --host / --port CLI flags
  2. APPLE_BRIDGE_HOST / APPLE_BRIDGE_PORT environment variables
  3. Defaults: 127.0.0.1:6002

From a Docker container on the same host, use the Docker bridge gateway IP,
e.g. `docker run ... reminders_client.py --host 192.168.65.2 --port 6002 list today`.

If the server was started with --api-key, set APPLE_BRIDGE_API_KEY (or
--api-key) and the header is sent automatically.

Usage:
  reminders_client.py list [today|tomorrow|week|overdue|all|YYYY-MM-DD]
  reminders_client.py lists
  reminders_client.py get <reminder-id>
  reminders_client.py create <title> [--list L] [--due D] [--alarm A]
  reminders_client.py complete <reminder-id>
  reminders_client.py delete <reminder-id>
  reminders_client.py status

Date formats for --due / --alarm / list filter:
  today, tomorrow, yesterday, YYYY-MM-DD, YYYY-MM-DD HH:mm, ISO 8601
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
    flt = args.filter or "all"
    status, data = request("GET", f"/reminders/v1/list?filter={urllib.parse.quote(flt)}",
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_lists(args):
    status, data = request("GET", "/reminders/v1/lists", host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_get(args):
    status, data = request("GET", f"/reminders/v1/get?id={urllib.parse.quote(args.id)}",
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_create(args):
    body = {"title": args.title}
    if args.list:
        body["list"] = args.list
    if args.due:
        body["due"] = args.due
    if args.alarm:
        body["alarm"] = args.alarm
    status, data = request("POST", "/reminders/v1/create", body=body,
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_complete(args):
    status, data = request("POST", "/reminders/v1/complete", body={"id": args.id},
                           host=args.host, port=args.port, api_key=args.api_key)
    print(json.dumps(data, indent=2))
    return 0 if status < 300 else 1


def cmd_delete(args):
    status, data = request("POST", "/reminders/v1/delete", body={"id": args.id},
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

    p = sub.add_parser("list", help="list reminders by date filter")
    p.add_argument("filter", nargs="?", default="all",
                   help="today|tomorrow|week|overdue|all|YYYY-MM-DD (default: all)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("lists", help="list reminder lists")
    p.set_defaults(func=cmd_lists)

    p = sub.add_parser("get", help="get one reminder by id")
    p.add_argument("id")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("create", help="create a reminder")
    p.add_argument("title")
    p.add_argument("--list", help="target reminder list name")
    p.add_argument("--due", help="due date (today, YYYY-MM-DD, 'YYYY-MM-DD HH:mm', ISO 8601)")
    p.add_argument("--alarm", help="notification time (same formats as --due)")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("complete", help="complete a reminder by id")
    p.add_argument("id")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("delete", help="delete a reminder by id")
    p.add_argument("id")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("status", help="show bridge status/endpoints")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
