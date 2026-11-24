#!/usr/bin/env python3
"""Tool statistics across every Claude Code and Codex transcript on disk.

For each tool call: name, server, category, input, result size in tokens, and flags.

Result tokens (Claude): each request records its exact input size in `usage`; the growth
between consecutive requests is what was added in between. That growth is split across the
added blocks by character count, so a tool result's tokens are measured per step and
apportioned inside it. Where the step can't be trusted (compaction, first request, last
result in a file, ratios outside 1-8 chars/token) the size falls back to chars / 3.
Codex results are always chars / 3 (marked estimated).

Flags:
  arg-heavier-than-default  an argument picks a heavier format than the schema's default
  result-heavier-than-default  the result carries fields the default format omits
                            (Gmail returning htmlBody when messageFormat was left unset)

Usage: tools.py [--out tools.json] [--workers 10] [--limit N]
"""

import argparse
import glob
import json
import os
import re
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
CLAUDE_ROOT = os.path.expanduser("~/.claude/projects")
CODEX_ROOTS = [
    os.path.expanduser("~/.codex/sessions"),
    os.path.expanduser("~/.codex/archived_sessions"),
]
CHARS_PER_TOKEN = 3.0
TOP_SAMPLES = 14
RECENT_SAMPLES = 8
FLAG_SAMPLES = 60
INPUT_CAP = 700
PREVIEW_CAP = 1200

HEAVY_VALUE = re.compile(r"^(FULL|FULL_CONTENT|RAW|HTML|VERBOSE|DETAILED|EXPANDED|ALL)$", re.I)
FORMAT_ARG = re.compile(r"format|detail|verbos|view|response_?mode|include|expand|level", re.I)
DEFAULT_IN_DESC = re.compile(r"[Dd]efaults? to `?([A-Za-z_]+)`?")

BUILTIN_CATEGORY = {
    "Read": "files",
    "Edit": "files",
    "Write": "files",
    "MultiEdit": "files",
    "Glob": "files",
    "Grep": "files",
    "NotebookEdit": "files",
    "NotebookRead": "files",
    "LS": "files",
    "apply_patch": "files",
    "view_image": "files",
    "read_file": "files",
    "list_dir": "files",
    "Bash": "shell",
    "BashOutput": "shell",
    "KillShell": "shell",
    "KillBash": "shell",
    "exec_command": "shell",
    "write_stdin": "shell",
    "shell": "shell",
    "local_shell": "shell",
    "unified_exec": "shell",
    "exec": "shell",
    "WebFetch": "web",
    "WebSearch": "web",
    "web_search": "web",
    "Agent": "agents",
    "Task": "agents",
    "Workflow": "agents",
    "SendMessage": "agents",
    "TaskOutput": "agents",
    "TaskStop": "agents",
    "ListAgents": "agents",
}


# ---------- naming ----------


def classify(name, namespace=None, source="claude"):
    """Return (server, category, display name)."""
    if namespace and namespace.startswith("mcp__"):
        server = namespace[5:].rstrip("_")
        return f"mcp · {server}", "mcp", name
    if namespace:
        return f"codex · {namespace}", "agents" if namespace == "collaboration" else "builtin", name
    if name.startswith("mcp__"):
        bits = name.split("__")
        server = bits[1] if len(bits) > 2 else "?"
        server = server.replace("claude_ai_", "claude.ai ")
        return f"mcp · {server}", "mcp", bits[-1]
    builtin = "claude code" if source == "claude" else "codex"
    return f"{builtin} built-in", BUILTIN_CATEGORY.get(name, "harness"), name


def result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif c.get("type") == "image":
                parts.append("[image]")
            else:
                parts.append(json.dumps(c)[:2000])
        return "\n".join(parts)
    if isinstance(content, str):
        return content
    return json.dumps(content) if content is not None else ""


def image_count(block):
    content = block.get("content")
    if isinstance(content, list):
        return sum(1 for c in content if c.get("type") == "image")
    return 0


# ---------- flags ----------


def schema_defaults(schema):
    """{arg: default} for enum-like args whose description names a default."""
    out = {}
    props = (schema or {}).get("input_schema", {}).get("properties", {}) or {}
    for arg, spec in props.items():
        if not isinstance(spec, dict):
            continue
        default = spec.get("default")
        if default is None:
            m = DEFAULT_IN_DESC.search(spec.get("description", "") or "")
            if m:
                default = m.group(1)
        if default is not None and FORMAT_ARG.search(arg):
            out[arg] = default
    return out


def flags_for(rec, defaults):
    return arg_flags(rec.get("input"), defaults) + rec.get("rflags", [])


def arg_flags(inp, defaults):
    flags = []
    inp = inp if isinstance(inp, dict) else {}
    for arg, value in inp.items():
        if not isinstance(value, (str, bool)) or not FORMAT_ARG.search(arg):
            continue
        default = defaults.get(arg)
        heavy = (isinstance(value, str) and HEAVY_VALUE.match(value)) or value is True
        if heavy and (default is None or str(value).lower() != str(default).lower()):
            note = f"{arg}={value}" + (f" (default {default})" if default is not None else "")
            flags.append({"kind": "arg-heavier-than-default", "note": note})
    return flags


def result_flags(name, inp, text):
    flags = []
    inp = inp if isinstance(inp, dict) else {}
    # Gmail: PLAIN_TEXT is the documented default and omits htmlBody.
    if re.search(r"Gmail__get_(thread|message)", name) and '"htmlBody"' in text:
        fmt = inp.get("messageFormat")
        if fmt != "FULL_CONTENT":
            html = sum(len(m) for m in re.findall(r'"htmlBody":\s*"((?:[^"\\]|\\.)*)"', text))
            how = "messageFormat unset" if fmt is None else f"messageFormat={fmt}"
            flags.append(
                {
                    "kind": "result-heavier-than-default",
                    "note": f"htmlBody returned with {how} (default PLAIN_TEXT omits it): "
                    f"~{round(html / CHARS_PER_TOKEN):,} tok of HTML",
                }
            )
    return flags


# ---------- per-file scans ----------


def scan_claude(path):
    is_sub = "/subagents/" in path
    calls = {}
    order = []
    schemas = {}
    pending = []
    prev_mid = None
    prev_total = None
    context_added = 0

    def close_step(total):
        nonlocal context_added
        if prev_total is None:
            return
        delta = total - prev_total
        chars = sum(c for c, _ in pending) or 1
        if delta > 0:
            context_added += delta
        for c, cid in pending:
            if cid is None or cid not in calls:
                continue
            rec = calls[cid]
            share = delta * c / chars if delta > 0 else 0
            ratio = c / share if share else 0
            if share and 1.0 <= ratio <= 8.0:
                rec["tokens"] = round(share)
                rec["measured"] = True

    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if '"api-request-shape"' in line[:200]:
                try:
                    shape = json.loads(line).get("shape", {})
                except json.JSONDecodeError:
                    continue
                for t in shape.get("tools", []) or []:
                    n = t.get("name")
                    if n and n not in schemas:
                        d = schema_defaults(t)
                        schemas[n] = d
                continue
            head = line[:400]
            is_assistant = '"type":"assistant"' in line[:2000] and '"usage"' in line
            is_user = '"tool_result"' in line or ('"type":"user"' in head)
            if not (is_assistant or is_user):
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("isSidechain") and not is_sub:
                continue
            kind = r.get("type")
            msg = r.get("message") or {}
            if kind == "assistant":
                mid = msg.get("id")
                u = msg.get("usage") or {}
                total = (
                    (u.get("input_tokens") or 0)
                    + (u.get("cache_read_input_tokens") or 0)
                    + (u.get("cache_creation_input_tokens") or 0)
                )
                if mid != prev_mid and total:
                    close_step(total)
                    pending = []
                    prev_total = total
                    prev_mid = mid
                for b in msg.get("content") or []:
                    if b.get("type") == "tool_use":
                        inp = b.get("input")
                        rec = {
                            "id": b.get("id"),
                            "name": b.get("name", "?"),
                            "input": inp,
                            "ts": r.get("timestamp", ""),
                            "file": path,
                            "src": "claude",
                        }
                        calls[rec["id"]] = rec
                        order.append(rec["id"])
                        pending.append((len(json.dumps(inp or {})), None))
                    elif b.get("type") == "text":
                        pending.append((len(b.get("text", "")), None))
                    elif b.get("type") == "thinking":
                        pending.append((len(b.get("thinking", "")), None))
            elif kind == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    pending.append((len(content), None))
                    continue
                for b in content or []:
                    if b.get("type") == "tool_result":
                        cid = b.get("tool_use_id")
                        text = result_text(b)
                        imgs = image_count(b)
                        rec = calls.get(cid)
                        if rec is not None:
                            rec["text"] = text
                            rec["chars"] = len(text)
                            rec["images"] = imgs
                            rec["error"] = bool(b.get("is_error"))
                            rec["tokens"] = round(len(text) / CHARS_PER_TOKEN) + imgs * 1600
                            rec["measured"] = False
                        pending.append((len(text) + imgs * 4800, cid))
                    elif b.get("type") == "text":
                        pending.append((len(b.get("text", "")), None))

    out = [calls[c] for c in order if "chars" in calls[c]]
    return {"calls": out, "schemas": schemas, "context_added": context_added}


def scan_codex(path):
    calls = {}
    order = []
    try:
        fh = open(path, "r", errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            if "_call" not in line[:400]:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = r.get("payload") or {}
            t = p.get("type")
            if t in ("function_call", "custom_tool_call"):
                raw = p.get("arguments") if t == "function_call" else p.get("input")
                try:
                    inp = (
                        json.loads(raw)
                        if isinstance(raw, str) and raw.strip().startswith("{")
                        else raw
                    )
                except json.JSONDecodeError:
                    inp = raw
                rec = {
                    "id": p.get("call_id"),
                    "name": p.get("name", "?"),
                    "namespace": p.get("namespace"),
                    "input": inp,
                    "ts": r.get("timestamp", ""),
                    "file": path,
                    "src": "codex",
                }
                calls[rec["id"]] = rec
                order.append(rec["id"])
            elif t in ("function_call_output", "custom_tool_call_output"):
                rec = calls.get(p.get("call_id"))
                if rec is None:
                    continue
                out = p.get("output")
                imgs = 0
                if isinstance(out, list):
                    # Inline screenshots are base64 data URLs; count them as images, not chars.
                    texts = []
                    for c in out:
                        if isinstance(c, dict) and c.get("type") in ("input_image", "image"):
                            imgs += 1
                            texts.append("[image]")
                        elif isinstance(c, dict):
                            texts.append(c.get("text", ""))
                    text = "\n".join(texts)
                else:
                    text = out if isinstance(out, str) else json.dumps(out)
                rec["text"] = text
                rec["chars"] = len(text)
                rec["images"] = imgs
                rec["error"] = False
                rec["tokens"] = round(len(text) / CHARS_PER_TOKEN)
                rec["measured"] = False
    return {
        "calls": [calls[c] for c in order if "chars" in calls[c]],
        "schemas": {},
        "context_added": 0,
    }


def finish(res):
    """Compute result flags while the full text is here, then keep only a preview."""
    for rec in res["calls"]:
        rec["rflags"] = result_flags(rec["name"], rec.get("input"), rec["text"])
        rec["text"] = rec["text"][: PREVIEW_CAP + 1]
        inp = rec.get("input")
        inp_s = inp if isinstance(inp, str) else json.dumps(inp)
        if len(inp_s) > 20000:
            rec["input"] = inp_s[:INPUT_CAP + 1]
    return res


def scan(path):
    try:
        res = scan_codex(path) if "/.codex/" in path else scan_claude(path)
        return finish(res) if res else res
    except Exception as exc:  # one bad file must not sink the sweep
        return {"calls": [], "schemas": {}, "context_added": 0, "error": f"{path}: {exc}"}


# ---------- aggregate ----------

def FORMAT_ARGS_SET(rec):
    """Calls that set any format-like argument; checked against schema defaults later."""
    inp = rec.get("input")
    return isinstance(inp, dict) and any(FORMAT_ARG.search(k) for k in inp)


def pctl(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def compact(rec, flags):
    inp = rec.get("input")
    inp_s = inp if isinstance(inp, str) else json.dumps(inp, indent=1)
    replay = None
    if rec["src"] == "claude":
        replay = f"replay.html?file={rec['file']}&tool={rec['id']}"
    return {
        "src": rec["src"],
        "ts": rec["ts"],
        "tokens": rec["tokens"],
        "measured": rec["measured"],
        "chars": rec["chars"],
        "error": rec.get("error", False),
        "file": rec["file"].replace(os.path.expanduser("~"), "~"),
        "input": inp_s[:INPUT_CAP] + ("…" if len(inp_s) > INPUT_CAP else ""),
        "preview": rec["text"][:PREVIEW_CAP] + ("…" if len(rec["text"]) > PREVIEW_CAP else ""),
        "flags": flags,
        "replay": replay,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "tools.json"))
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 2))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = glob.glob(f"{CLAUDE_ROOT}/**/*.jsonl", recursive=True)
    for root in CODEX_ROOTS:
        files += glob.glob(f"{root}/**/rollout-*.jsonl", recursive=True)
    files.sort(key=lambda f: -os.path.getsize(f))
    if args.limit:
        files = files[: args.limit]
    print(f"scanning {len(files)} transcripts with {args.workers} workers", file=sys.stderr)

    started = time.time()
    tools = {}
    schemas = {}
    context_added = 0
    claude_tokens = 0
    errors = []
    done = 0

    with Pool(args.workers) as pool:
        for res in pool.imap_unordered(scan, files, chunksize=4):
            done += 1
            if done % 2000 == 0:
                print(f"  {done}/{len(files)} in {time.time() - started:.0f}s", file=sys.stderr)
            if not res:
                continue
            if res.get("error"):
                errors.append(res["error"])
            context_added += res["context_added"]
            for n, d in res["schemas"].items():
                if d:
                    schemas.setdefault(n, d)
            for rec in res["calls"]:
                if rec["src"] == "claude":
                    claude_tokens += rec["tokens"]
                server, category, display = classify(rec["name"], rec.get("namespace"), rec["src"])
                key = f"{server}|{display}"
                t = tools.setdefault(
                    key,
                    {
                        "server": server,
                        "category": category,
                        "name": display,
                        "full": rec["name"]
                        if not rec.get("namespace")
                        else f"{rec['namespace']}.{rec['name']}",
                        "calls": 0,
                        "tokens": 0,
                        "max": 0,
                        "measured": 0,
                        "errors": 0,
                        "claude": 0,
                        "codex": 0,
                        "flagged": 0,
                        "flagTokens": 0,
                        "top": [],
                        "recent": [],
                        "flagSamples": [],
                    },
                )
                t["calls"] += 1
                t.setdefault("sizes", []).append(rec["tokens"])
                t["tokens"] += rec["tokens"]
                t["max"] = max(t["max"], rec["tokens"])
                t["measured"] += rec["measured"]
                t["errors"] += rec.get("error", False)
                t[rec["src"]] += 1
                # Flags are computed after schemas merge; stash the raw record cheaply.
                rec["_key"] = key
                t["top"].append(rec)
                if len(t["top"]) > TOP_SAMPLES * 4:
                    t["top"].sort(key=lambda x: -x["tokens"])
                    del t["top"][TOP_SAMPLES * 2 :]
                t["recent"].append(rec)
                if len(t["recent"]) > RECENT_SAMPLES * 4:
                    t["recent"].sort(key=lambda x: x["ts"], reverse=True)
                    del t["recent"][RECENT_SAMPLES:]
                if rec["rflags"] or arg_flags(rec.get("input"), {}) or FORMAT_ARGS_SET(rec):
                    t["flagSamples"].append(rec)
                    if len(t["flagSamples"]) > 2000:
                        t["flagSamples"].sort(key=lambda x: -x["tokens"])
                        del t["flagSamples"][1000:]

    # Final flags use the merged schema defaults.
    total_tokens = sum(t["tokens"] for t in tools.values())
    out_tools = []
    for key, t in tools.items():
        defaults = schemas.get(t["full"], {})
        flagged = []
        for rec in t["flagSamples"]:
            f = flags_for(rec, defaults)
            if f:
                flagged.append((rec, f))
        t["flagged"] = len(flagged)
        t["flagTokens"] = sum(r["tokens"] for r, _ in flagged)
        flagged.sort(key=lambda rf: -rf[0]["tokens"])

        top = sorted(t["top"], key=lambda x: -x["tokens"])[:TOP_SAMPLES]
        recent = sorted(t["recent"], key=lambda x: x["ts"], reverse=True)[:RECENT_SAMPLES]
        seen = set()
        samples = []
        for rec, f in flagged[:FLAG_SAMPLES]:
            seen.add(id(rec))
            samples.append(compact(rec, f))
        for rec in top + recent:
            if id(rec) in seen:
                continue
            seen.add(id(rec))
            samples.append(
                compact(rec, flags_for(rec, defaults))
            )

        out_tools.append(
            {
                "server": t["server"],
                "category": t["category"],
                "name": t["name"],
                "full": t["full"],
                "calls": t["calls"],
                "tokens": t["tokens"],
                "avg": round(t["tokens"] / t["calls"]),
                "max": t["max"],
            "p50": pctl(t["sizes"], 0.5),
            "p90": pctl(t["sizes"], 0.9),
                "share": t["tokens"] / total_tokens if total_tokens else 0,
                "measuredShare": t["measured"] / t["calls"],
                "errors": t["errors"],
                "claude": t["claude"],
                "codex": t["codex"],
                "flagged": t["flagged"],
                "flagTokens": t["flagTokens"],
                "defaults": defaults,
                "samples": samples,
            }
        )

    out_tools.sort(key=lambda t: -t["tokens"])
    data = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "files": len(files),
        "seconds": round(time.time() - started),
        "toolTokens": total_tokens,
        "contextAdded": context_added,
        "claudeToolTokens": claude_tokens,
        "calls": sum(t["calls"] for t in out_tools),
        "errors": errors[:20],
        "tools": out_tools,
    }
    with open(args.out, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))

    print(
        f"{data['calls']:,} calls, {len(out_tools)} tools, {total_tokens:,} result tokens "
        f"({100 * total_tokens / max(1, context_added):.0f}% of {context_added:,} Claude context "
        f"added) in {data['seconds']}s; {len(errors)} file errors",
        file=sys.stderr,
    )
    for t in out_tools[:25]:
        print(
            f"{t['tokens']:>12,} {t['calls']:>8,} avg {t['avg']:>6,} {100 * t['share']:5.1f}% "
            f"flag {t['flagged']:>4} {t['server'][:26]:26} {t['name']}"
        )


if __name__ == "__main__":
    main()
