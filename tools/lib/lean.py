"""Shared plumbing for the lean connector tools (gmail-thread, gcal-agenda, linear-find, ...).

Not a tool itself: it lives in lib/, which the runner skips. Tools import it with lib/ on sys.path.

The contract every lean tool follows (see ../README.md):
- compact text by default, one record per line, the fields an agent actually reads,
  "0 results" when empty, and a "more:" note on stderr when cut off;
- --json prints NDJSON, one flat object per line, empty values omitted;
- --fields a,b,c picks and orders columns from a closed, tool-owned set (unknown: exit 2);
- --max-chars caps every free-text field, so no single result can flood a context window;
- the raw API bytes go to $TOOL_METRICS (when set) so a runner can count what was saved.
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser

DEFAULT_MAX_CHARS = 400

_raw_bytes = 0


class ToolError(Exception):
    """A failure the caller should see as one line on stderr, not a traceback."""


# ---------------------------------------------------------------- argument parsing


def parser(description, default_fields, all_fields):
    """argparse with the shared output flags already attached."""
    p = argparse.ArgumentParser(description=description)

    p.add_argument("--json", action="store_true", help="NDJSON, one flat object per line")
    p.add_argument(
        "--fields",
        default=",".join(default_fields),
        help="comma list, in order; available: " + ",".join(all_fields),
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help="cap on each free-text field (0 = no cap)",
    )

    return p


def main(run):
    """Run a tool body, turning ToolError into a one-line message and exit 1."""
    try:
        run()
    except ToolError as exc:
        print(f"{os.path.basename(sys.argv[0])}: {exc}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        pass
    finally:
        _flush_metrics()


# ---------------------------------------------------------------- output


def cap(text, limit):
    """Collapse whitespace and cut to limit chars, marking the cut."""
    if text is None:
        return ""

    flat = re.sub(r"\s+", " ", str(text)).strip()

    if limit and len(flat) > limit:
        return flat[: limit - 1].rstrip() + "…"

    return flat


def selected_fields(args, all_fields):
    """--fields as a list; an unknown name is a usage error (exit 2) listing the valid ones."""
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    unknown = [f for f in fields if f not in all_fields]

    if unknown:
        print(
            f"unknown field(s): {','.join(unknown)}; valid: {','.join(all_fields)}",
            file=sys.stderr,
        )
        sys.exit(2)

    return fields


def emit(rows, args, all_fields, text_fields=(), more=None):
    """Print rows in the shared contract.

    text_fields are capped by --max-chars. more is a hint shown when results were cut off,
    e.g. "--limit 50". It goes to stderr: an agent still sees it in the tool result, and a
    script's $(...) capture stays pure data.
    """
    fields = selected_fields(args, all_fields)
    shaped = []

    for row in rows:
        picked = {}

        for name in fields:
            value = row.get(name)

            if name in text_fields:
                value = cap(value, args.max_chars)

            picked[name] = value

        shaped.append(picked)

    if args.json:
        for row in shaped:
            print(json.dumps(_drop_empty(row), ensure_ascii=False))
    elif not shaped:
        print("0 results")
    else:
        for row in shaped:
            print("  ".join(_text_cell(row[name]) for name in fields))

    if more:
        print(f"# {len(shaped)} shown, more: {more}", file=sys.stderr)


def _drop_empty(row):
    return {k: v for k, v in row.items() if v not in (None, "", [])}


def emit_blocks(blocks, args):
    """Print long records (thread messages, docs): a header line, then the body.

    blocks is a list of (header dict, body text). --json gives one array of objects.
    """
    if args.json:
        for header, body in blocks:
            print(json.dumps(_drop_empty({**header, "body": body}), ensure_ascii=False))
        return

    if not blocks:
        print("0 results")
        return

    for header, body in blocks:
        print("  ".join(_text_cell(v) for v in header.values()))

        if body:
            print(body)

        print()


def _text_cell(value):
    if isinstance(value, list):
        return ",".join(str(v) for v in value) or "-"

    if value is None or value == "":
        return "-"

    return str(value)


# ---------------------------------------------------------------- metrics


def count_raw(nbytes):
    """Add raw API bytes processed; reported once at exit."""
    global _raw_bytes
    _raw_bytes += nbytes


def _flush_metrics():
    if not _raw_bytes:
        return

    path = os.environ.get("TOOL_METRICS")

    if not path:
        return

    try:
        with open(path, "w") as fh:
            fh.write(str(_raw_bytes))
    except OSError:
        pass


# ---------------------------------------------------------------- transports


def gws(*argv, params=None):
    """Call the gws CLI and return parsed JSON. Raw response size counts toward metrics."""
    cmd = ["gws", *argv]

    if params is not None:
        cmd += ["--params", json.dumps(params)]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise ToolError("gws not installed (brew install gws)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("gws timed out after 60s") from exc

    if proc.returncode != 0:
        raise ToolError(f"gws failed: {_first_error_line(proc.stderr or proc.stdout)}")

    count_raw(len(proc.stdout.encode()))

    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise ToolError("gws returned non-JSON output") from exc


def _first_error_line(text):
    lines = [ln for ln in text.splitlines() if ln.strip() and "keyring backend" not in ln]
    return cap(" ".join(lines[:3]), 300) or "no detail"


LINEAR_API = os.environ.get("LINEAR_API_URL", "https://api.linear.app/graphql")


def linear_token():
    """The Linear API key from the environment. Never printed."""
    token = os.environ.get("LINEAR_API_KEY")

    if not token:
        raise ToolError("no Linear key: set LINEAR_API_KEY")

    return token


def linear(query, variables=None):
    """POST one GraphQL document to Linear and return its data."""
    body = json.dumps({"query": query, "variables": variables or {}}).encode()

    # Personal API keys go bare, without "Bearer".
    req = urllib.request.Request(
        LINEAR_API,
        data=body,
        headers={"Content-Type": "application/json", "Authorization": linear_token()},
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200] if exc.fp else ""
        raise ToolError(f"Linear HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ToolError(f"Linear unreachable: {exc}") from exc

    count_raw(len(raw))
    payload = json.loads(raw)

    if payload.get("errors"):
        raise ToolError("Linear: " + cap(payload["errors"][0].get("message"), 200))

    return payload["data"]


# ---------------------------------------------------------------- text cleanup


class _TextExtractor(HTMLParser):
    _SKIP = {"style", "script", "head", "title"}
    _BREAK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip_depth += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)


def html_to_text(markup):
    """Visible text of an HTML body, blank runs squeezed. Stdlib only, by design."""
    extractor = _TextExtractor()
    extractor.feed(markup or "")

    text = html.unescape("".join(extractor.parts))
    text = re.sub(r"[ \t ‌͏]+", " ", text)

    return re.sub(r"\n\s*\n+", "\n", text).strip()


_QUOTE_MARKERS = (
    re.compile(r"^On .{0,200}wrote:\s*$", re.M),
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.M | re.I),
    re.compile(r"^From: .*\n(Sent|Date): ", re.M),
)


_URL = re.compile(r"https?://([^\s/?#>\"']+)([^\s?#>\"']*)[^\s>\"']*")


def short_urls(text):
    """Tracking links are most of a marketing body: keep host and path, drop the query."""

    def _short(match):
        path = match.group(2)

        if len(path) > 40:
            path = path[:39] + "…"

        return match.group(1) + path

    return _URL.sub(_short, text)


def strip_quoted(text):
    """Drop the quoted history under a reply; the earlier messages are in the thread already."""
    cut = len(text)

    for marker in _QUOTE_MARKERS:
        match = marker.search(text)

        if match:
            cut = min(cut, match.start())

    lines = [ln for ln in text[:cut].splitlines() if not ln.startswith(">")]

    return "\n".join(lines).strip()
