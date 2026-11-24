# Context Budget

<!-- site:summary -->

Every tool result an agent reads stays in its context window for the rest of the session. This
chapter measures where those tokens actually go across 18,873 Claude Code and Codex transcripts,
then covers the three fixes that came out of it: small command-line tools that replace connector
reads, a hook that trims oversized MCP results, and a context guard that rotates sessions at a
clean break instead of letting autocompact summarise.

<!-- /site:summary -->

## The Problem

One Claude Code session read a one-sentence LinkedIn message through the claude.ai Gmail
connector and got back 35,887 characters, nearly all of it HTML: style blocks, layout markup and
tracking links. The connector's `get_thread` schema says plain text is the default. Leaving the
format out returned the HTML body anyway.

The obvious conclusion is that MCP connectors are what bloats agent context. Measuring first
said otherwise.

## Measure Before Rebuilding

[`scripts/tool-stats`](../../scripts/tool-stats) walks every Claude Code and Codex transcript on
a machine and sizes each tool result. Claude Code logs each request's input size, so the growth
between two requests can be split across the results that arrived in between. Codex doesn't, so
its results are estimated from characters. Treat the totals as estimates. They count tool
results only, not the tool definitions that load before anything is called.

On one heavy-use machine (18,873 transcript files, 270,405 tool calls, about 439M tool-result
tokens):

| Tool                                          | Share of tool-result tokens |
| --------------------------------------------- | --------------------------: |
| Bash                                          |                         48% |
| Read                                          |                         35% |
| Every MCP connector combined                  |                        3.6% |
| Everything else (Codex shell, web, subagents) |                         13% |

MCP is the small slice. Most context goes to shell output and file reads, which are the
operator's own scripts and files, so they're also the easiest to change.

MCP is still 15.9M tokens, and it is uneven. Gmail search was the worst single tool (about 2M
tokens over 503 calls), then a self-written memory server, then Gmail `get_thread`, Linear
`list_issues` and Calendar `list_events` (about 5.5K tokens a call, because every event carries
its HTML description, conference data and reminders). The pattern is the same throughout: the
connector returns the API's whole object and the agent reads a few fields of it.

## Fix 1: Own the Read Path

[`tools/`](../../tools) holds five small CLIs that replace connector reads for Gmail, Calendar,
Drive and Linear. Both Claude Code and Codex call them through the shell; Codex can't use
claude.ai connectors at all, so a CLI is the one thing both clients share. On the same requests:

| Request                       | Connector (chars) | Tool (chars) |
| ----------------------------- | ----------------: | -----------: |
| One Gmail thread              |            35,887 |        1,688 |
| A week of calendar, 12 events |            14,085 |          802 |
| 25 Linear issues              |            35,216 |        2,952 |

They share one contract: text by default, `--json` for NDJSON, a closed `--fields` set, a
`--max-chars` cap on every free-text field, and raw-byte metrics so savings are counted rather
than guessed. They're read-only on purpose: every expensive call measured was a read, and a write
through the shell would lose the connector's per-tool permission prompt.

`tools/mcp-inventory` ranks every MCP tool in the stats by tokens against a hand-kept map of what
replaces it. On the source machine: 38% of MCP result tokens are covered by these tools, 21% was
a self-written server to fix at the source, 15% had an existing CLI or was retired, and 25% stays
(browser and screenshot tools need to see the screen).

## Fix 2: Guard the Connectors You Keep

[`hooks/mcp-result-guard`](../../hooks/mcp-result-guard) is one Claude Code hook with two jobs.
Before a Gmail `get_thread` or `get_message` that didn't pick a format, it adds
`messageFormat: PLAIN_TEXT`; an explicit format is left alone and the normal permission flow
still runs. After any MCP result over 20,000 characters, it saves the full result to disk and
returns a trimmed copy whose first line names what was cut and where the full copy is. HTML
bodies are dropped for mail tools only; images and other tools' markup are left alone.

Replaying the trim over a recorded `get_thread` took it from 24,151 characters to 1,979, at 30
to 40 ms a run. It fails open. The tools fix the shape at the source; the hook trims after the
fact. Prefer the first wherever it exists, and keep the second as the net.

An instruction alone didn't hold. "Never ask Gmail for full content" was followed, and still
missed, because the bad calls left the format out.

## Fix 3: Rotate, Don't Compact

[Context guard](context-guard.md) watches context growth from the statusline and hands off at a
clean break: a gate on growth since the session's first response, a ceiling on absolute size,
fire-once markers, a frozen predecessor, and a holder protocol so background work survives the
rotation. Those are design notes from a working private version.

## Next: Code Mode

Claude Code's experimental code mode (`CLAUDE_CODE_EXPERIMENTAL_CODE_MODE=1`) has the model write
a program that calls its tools, so in principle only the program's return value lands in
context. As of 2.1.331 it is chosen at session start, turns off Claude in Chrome, computer use and
Remote Control, and refuses some enterprise setups. It is unmeasured here; it would fit
data-heavy sessions such as inbox triage.

## What Isn't Known Yet

- Whether agents reach for the tools without being told. Each run is logged against the
  connector calls it replaces, so a flat connector share means the rule isn't working.
- What tool definitions cost. A connector's schema loads before it's ever called, and that cost
  isn't in the 439M.
