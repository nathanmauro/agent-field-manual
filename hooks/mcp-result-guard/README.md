# mcp-result-guard

`mcp-result-guard` is one Claude Code hook with two jobs. It keeps MCP tool results from filling
the context window with HTML, tracking links and base64. The payload's `hook_event_name` picks
the mode.

| Event         | Matcher                                                    | What it does                                                   |
| ------------- | ---------------------------------------------------------- | -------------------------------------------------------------- |
| `PreToolUse`  | `mcp__claude_ai_Gmail__get_thread` and `..._get_message`   | Adds `messageFormat: PLAIN_TEXT` when the call leaves it unset |
| `PostToolUse` | `mcp__.*`                                                  | Saves any MCP result over 20000 chars and returns a trim       |

## Install

```sh
mkdir -p ~/.claude/hooks
cp mcp-result-guard ~/.claude/hooks/
chmod +x ~/.claude/hooks/mcp-result-guard
```

Then add both entries to `~/.claude/settings.json` (merge with any `hooks` you already have):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__claude_ai_Gmail__get_thread|mcp__claude_ai_Gmail__get_message",
        "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/hooks/mcp-result-guard", "timeout": 5 }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "mcp__.*",
        "hooks": [{ "type": "command", "command": "python3 -I ~/.claude/hooks/mcp-result-guard", "timeout": 5 }]
      }
    ]
  }
}
```

Hooks load when a session starts, so restart `claude` after editing settings. If your Gmail
connector has a different tool prefix, change the `PreToolUse` matcher to match it.

## Why

- Gmail `get_thread` called with only `threadId` returns `htmlBody`: about 20K chars of a 24K
  result for a one-sentence LinkedIn InMail. Its schema says the default is PLAIN_TEXT. An
  explicit `messageFormat: PLAIN_TEXT` returns about 2.5K and no `htmlBody`. `get_message`
  behaves the same way (23.7K by default).
- Other large MCP results seen in real use (chars): a local memory server's recall p90 17K and
  session listing median 32K, Calendar `list_events` p90 25K, Gmail `search_threads` up to 49K.
- Replaying the hook over recent real transcripts: `get_thread` 24151 -> 1979 chars,
  `get_message` 23761 -> 1667, `search_threads` 49023 -> 18943, Calendar `list_events` 26141 ->
  19654. Each run took about 30-40 ms.
- For scale: across 18,873 transcripts, MCP results were 3.6% of all tool-result tokens (Bash
  48%, Read 35%). MCP is not the biggest spender overall. The problem is the single call that
  costs 10x what it carries, and those are the ones this hook catches.

## PreToolUse: ask Gmail for plain text

When `tool_input.messageFormat` is missing, empty or `MESSAGE_FORMAT_UNSPECIFIED`, the hook
returns:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "updatedInput": { "...": "every original field", "messageFormat": "PLAIN_TEXT" }
  }
}
```

It leaves out `permissionDecision` on purpose. Claude Code 2.1.331 applies an `updatedInput`
that has no decision as a plain input rewrite, and the normal permission flow then runs on the
new input. `allow` would skip the permission check and `ask` would always prompt. An explicit
`FULL_CONTENT`, `MINIMAL`, `METADATA_ONLY` or `PLAIN_TEXT` is left alone, so asking for HTML still
works.

With `CLAUDE_CODE_RESTRICT_PERSONAL_CONFIG` set, Claude Code denies any call whose input a user
hook changes, so the hook skips the rewrite in that mode.

## PostToolUse: the size guard

The hook measures the model-visible text: the string itself, or the sum of the `text` blocks in
a content-block array. Image and other blocks do not count. At or under the threshold the hook
prints nothing. Over it, the hook:

1. Saves `{tool_name, tool_use_id, session_id, saved_at, tool_input, tool_response}` to
   `~/.claude/tool-output-full/<session_id>/<tool_use_id>.json` (directory 0700, file 0600). If
   the save fails, the hook leaves the result alone, so the header never points at a missing file.
2. Trims each text part. JSON is parsed, scrubbed and written back compact:
   - `htmlBody` (and `html_body`, `bodyHtml`, `body_html`) is dropped at any depth. If nothing
     beside it holds a plain-text body, it is kept as tag-stripped text instead.
   - `data:...;base64,` URIs and bare base64 runs over 200 chars become
     `[image/png base64, N chars removed]` or `[base64, N chars removed]`.
   - Query strings come off `http(s)` URLs longer than 120 chars (`...?…`). A `#fragment` after
     the query survives, because Gmail view links carry the thread and message ids there.
   - Runs of zero-width and combining-joiner padding (the U+034F filler in Gmail snippets)
     collapse to one space. Runs of three or more blank lines become one blank line. A match
     must start on an invisible character, so long plain whitespace runs (padded tables, page
     text) stay linear; the old pattern took 2.9 s on 30K spaces.
   - For mail tools only (tool name contains `mail`), text that looks like an HTML document
     (over 1000 chars, at least 20 tags) is stripped to text. Everywhere else markup is content
     (a Vue file from a GitHub server, a Chrome outerHTML dump, a Linear doc) and is kept.
3. If the text is still over the threshold, it is cut at the last line break, JSON value end
   (`},` `],` `,"`) or space within 2000 chars of the limit. A
   `…[mcp-result-guard] cut N chars here` marker follows the cut.
4. Puts one header line first, then the trimmed result. The total stays within the threshold:

   ```text
   [mcp-result-guard] trimmed <orig> -> <new> chars; full result: <path> (read it with the Read
   tool if you need the rest). Lossy edits: <list>.
   ```

   The list names every kind of edit with its count, for example "1 HTML body field removed, 4
   URL query strings removed, 1 text cut short", so a trimmed result never passes for the
   original. The real header is one line.

The output goes back in the original result's shape: a string stays a string, and a block array
stays a block array (the header becomes a new first text block, and images are unchanged):

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "updatedToolOutput": "<string or [blocks]>"
  }
}
```

Notes on Claude Code 2.1.331's handling of `updatedToolOutput`:

- Claude Code prefers `updatedToolOutput` to `updatedMCPToolOutput` and reads the second only when
  the first is absent. `updatedMCPToolOutput` is also ignored when it is falsy.
- For MCP tools, Claude Code accepts any value. The output-shape check (`PostToolUse hook
returned updatedToolOutput that does not match ...`) runs only for built-in tools.
- MCP results come from claude.ai connectors as one string (Gmail, Calendar) and from local
  servers as `[{type:"text"|"image",...}]` (most local servers, Chrome). The MCP tool's own schema is
  string | block array | unknown. The hook rewrites only the first two shapes and passes
  anything else through.
- Claude Code drops a rewrite for tools it protects, such as the account memory server, docs
  connectors and computer-use results in some modes. It logs a warning, and the original output
  is used.
- PostToolUse hooks run in parallel on the original output. This is the only hook here that
  rewrites output.

## Knobs

| Env var                      | Default | Effect                                                                               |
| ---------------------------- | ------- | ------------------------------------------------------------------------------------ |
| `MCP_RESULT_GUARD_MAX_CHARS` | `20000` | Threshold and output budget, in chars (floor 2000)                                   |
| `MCP_RESULT_GUARD_KEEP_DAYS` | `14`    | Full copies older than this are deleted each time one is saved; `0` keeps everything |
| `MCP_RESULT_GUARD_OFF`       | unset   | `1` turns both modes off                                                             |

The hook reads these from the Claude Code process environment, so set them in the shell or in
`settings.json` `env`.

## Turning it off

- For now: `export MCP_RESULT_GUARD_OFF=1` before starting `claude`, or add it to the `env`
  block in `settings.json`.
- For good: remove both `mcp-result-guard` entries from `~/.claude/settings.json` and delete
  `~/.claude/hooks/mcp-result-guard`.

## Full copies hold personal data

The saved files are whole mail threads, calendars and notes. They are owner-only (0600). Each
save deletes copies older than `MCP_RESULT_GUARD_KEEP_DAYS` (14) and removes empty session
directories. To wipe them now, run `rm -rf ~/.claude/tool-output-full`.

## Failure behavior

The hook fails open. A malformed payload, a non-object payload, an unknown tool or result shape,
a failed save or any exception exits 0 with no output, and the call and result go through
unchanged. It uses the Python 3 stdlib only and runs as `python3 -I` (tested on 3.9 and 3.14).

## Tests

`bash tests/test.sh` pipes payloads into the hook with a temp `HOME`. The fixtures in
`tests/fixtures/` are placeholder data in the shapes of real transcript results. Regenerate them
with `python3 -I tests/fixtures/generate.py tests/fixtures`. Needs `jq`.
