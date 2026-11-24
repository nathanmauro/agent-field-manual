#!/usr/bin/env python3
"""Serve the tool stats page on 127.0.0.1.

  /             tools.html, reading tools.json from this directory (run tools.py first)
  /api/library  stats from a script library CLI, when one is installed

The library tab calls `$TOOL_LIBRARY_CLI stats --json` and `$TOOL_LIBRARY_CLI list`. Leave the
variable unset and the tab says no library is installed; the tools tab works either way.

Usage: serve.py [--port 8742]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))


def library():
    name = os.environ.get("TOOL_LIBRARY_CLI", "")
    exe = shutil.which(name) if name else None
    if not exe:
        return {"available": False, "error": "no script library CLI (set TOOL_LIBRARY_CLI)"}

    def run(*args):
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=20)
        if out.returncode != 0:
            raise RuntimeError(f"{name} {' '.join(args)}: {out.stderr.strip()[:300]}")
        return out.stdout

    stats = json.loads(run("stats", "--json"))
    listing = run("list")
    return {"available": True, "stats": stats, "list": listing, "exe": exe}


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            self.path = "/tools.html"
            return super().do_GET()
        if url.path != "/api/library":
            return super().do_GET()

        try:
            body = json.dumps(library()).encode()
        except Exception as exc:
            body = json.dumps(
                {"available": False, "error": f"{type(exc).__name__}: {exc}"}
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8742)
    args = ap.parse_args()

    handler = partial(Handler, directory=HERE)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"http://127.0.0.1:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
