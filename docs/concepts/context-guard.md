# Context Guard

<!-- site:summary -->

Long sessions get vaguer well before autocompact fires, and autocompact then summarises away the
wrong things. A context guard treats context as a budget: it measures growth from the
statusline, marks the session due, and hands off to a fresh session at a clean break. These are
design notes from a working private version.

<!-- /site:summary -->

These are design notes, not a drop-in tool yet. The working version runs in a private setup and
depends on two pieces you would swap for your own: a handoff store (where a session writes what
the next one needs) and a terminal launcher (what opens the next session). What carries over is
the shape below, which took several rounds of real use to get right.

## The problem

Long Claude Code sessions get vaguer well before autocompact fires. Then autocompact summarises
away the wrong things: the decision you made an hour ago survives as one line, and the dead end
you already ruled out comes back. The fix is to treat context like a budget. Measure it, mark the
session due when it has spent enough, and start a fresh session from a written handoff at a moment
when nothing is in the middle of happening.

## Signals Claude Code actually exposes

Checked against the binary and live payloads (Claude Code 2.1.3xx):

| Source                          | Context size?                                                   | Use                    |
| ------------------------------- | --------------------------------------------------------------- | ---------------------- |
| Statusline stdin JSON           | Yes: `context_window.current_usage`, `context_window_size`      | The sensor             |
| Hook stdin JSON (every event)   | No. Session id, transcript path, cwd, agent id, and so on       | The actuator only      |
| Hook output `additionalContext` | Reaches the model                                               | Tell Claude            |
| Hook output `systemMessage`     | Shown to the person                                             | Tell the person        |
| `CLAUDE_CODE_ENTRYPOINT`        | `sdk-cli` under `-p`, `cli` when interactive                    | Skip headless sessions |
| Transcript `message.usage`      | Same numbers, but undocumented, duplicated rows, no window size | Rejected as the sensor |
| `PreCompact` hook               | Fires once compaction has already started                       | Too late               |

So the statusline script writes the current reading to a small per-session file, and the hooks
read that file. Occupancy is the sum of `current_usage` (input + cache creation + cache read).
Don't rely on `total_input_tokens`: older builds used that name for a cumulative total.

## Two thresholds

- **Gate: growth since the session's first response** (default 200k). A heavy first prompt is
  not this session's work, so the gate is charged only for what the session added. Crossing it
  only marks the session due.
- **Ceiling: absolute size** (default 300k). Cost per turn and answer quality follow the whole
  window, so nothing is subtracted. Past it, Claude takes on no new unit of work: it checkpoints,
  commits and ends the turn, then hands off.

Both cap at a share of the window (70% and 80%), so on a 200k-window model they land under
autocompact. Per-model overrides help: the priciest model gets lower numbers because every turn
re-reads the whole context.

Turn Claude Code's own autocompact off when the guard is on. A compaction backstop sounds safe,
but after a compaction the guard re-arms and allows another full gate on a lossy summary.

## Rotate only at a clean break

A clean break is the turn ending (the `Stop` hook) or the person sending a prompt. Never
mid-turn: `PostToolUse` only marks the session due or tells it to drain.

At the clean break, if the session is due, at least 30 minutes old, and has made progress, the
`Stop` hook blocks once (`decision: block`) with the handoff instructions. That is the one place
blocking a stop is right, because "continue and hand off" is exactly what you want. A marker
per level plus `stop_hook_active` keeps it from looping.

**Progress** means a commit since the session began, on any local branch, or an explicit
`progress` mark for things git can't see. A session that has done nothing since its last handoff
never rotates at the gate. That one rule prevents most rotation loops.

## Fire once per crossing

Each threshold level claims a marker file with `O_EXCL` (`set -o noclobber` in bash). Twelve
parallel hook calls produce one handoff. A JSON state file with read-modify-write double-fires
under parallel tool calls; marker files don't.

## Refresh in place, or a new session when work is live

The default rotation clears the same terminal and sends the handoff as the first prompt. That
is wrong when background work is live: `/clear` does not stop background agents or shells, and
their results land in a conversation that no longer knows why it started them. So:

- **No live work:** refresh in place.
- **Live work:** open a new session for the handoff and leave the old one running as a
  **holder**. The holder starts and stops nothing and answers each completion notice with one
  line naming who has the work now.
- **The new session watches from disk.** Each in-flight item gets a background watcher that
  waits for the item's ending in the old session's transcript or task files, then relays it.
  A background shell beats a `Monitor` here, because monitors expire.
- **Count only main-transcript launches.** A notification quoted inside a tool result is not a
  launch. Agent-team teammates count as in flight until they shut down, and an agent's interim
  ending doesn't count while its own background task still runs. Both were real loss paths.

## Freeze the predecessor

Give the successor its session id before launch (`claude --session-id`), then freeze the old
session before the new one starts. A `PreToolUse` hook denies writes from a frozen session:
file edits, commits and pushes. Without it, two sessions can write to the same checkout. The
hook fails open on any error, so a bug in the guard never blocks real work.

## Warn on thrash

A third launch in an hour, or a second handoff in a row with no progress, sends a warning and
rotates anyway. Nothing ever stops a rotation; the warning is there so a person notices a loop.

## What the handoff carries

The model writes the prose (what was done, open loops, the single next action). The guard adds
what it can know mechanically: dirty paths, branch and HEAD, and the list of in-flight items with
their watcher commands. Keep one record of the handoff. A file plus a database copy drifts.

## Deliberately left out

- Codex: it exposes no context signal to a statusline, so the sensor doesn't port. The handoff
  half would.
- Per-question picker timeouts: the setting is global and read once when the dialog mounts.
- IDE surfaces that don't render the statusline: the guard stays silent there.
