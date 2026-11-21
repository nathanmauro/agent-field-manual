# Native Swarms

<!-- site:summary -->

Some agent clients now ship a native swarm: one coordinator posts work to a shared board, and
several workers claim, run, and report on it inside the client. For fan-out across repositories,
browser work, and workspace operations, that board replaced a shell-supervised worker bus that
had to be launched, watched, and reaped by hand. This chapter covers when a native swarm is the
right engine, when deep single-repo coding should leave it, and the one isolation rule both
shapes share: workers that edit files never share a checkout.

<!-- /site:summary -->

## The Problem

Fan-out used to mean building the coordination yourself. A conductor session started workers in
terminal panes or headless processes, sent each one a turn over a supervised channel, watched
each turn for a terminal result, and collected the envelopes. That works, and the
[supervised delegation](supervised-delegation.md) contract is still the right shape when the
worker is another vendor's client. But for wide, shallow work (sweep twelve repositories for
stale worktrees, check the status of every open branch, clean up a cloud project, walk a set of
web pages) most of the conductor's tokens went to supervision, not to the work.

## The Pattern

A native swarm moves the coordination into the client. The coordinator writes tasks to a board;
workers claim them, run with their own context, and post results back to the same board. The
client tracks liveness and completion, so the conductor reads one board instead of polling N
turns.

| Property             | Shell-supervised worker bus               | Native swarm board                         |
| -------------------- | ----------------------------------------- | ------------------------------------------ |
| Who launches workers | The conductor, one `start` per worker     | The client, from tasks on the board        |
| Who tracks state     | A runner per turn, exit codes, envelopes  | The board: claimed, running, done, blocked |
| Cross-vendor         | Yes; the point of the channel             | No; workers run in the same client         |
| Supervision cost     | Grows with workers and turns              | Roughly one board read                     |
| Best at              | Bounded builds by another vendor's model  | Wide, shallow fan-out and operations       |
| Writes to `.git`     | Never; the sandbox keeps `.git` read-only | Per policy; prefer the conductor commits   |

The split that came out of real use is a lane per engine:

- **The swarm client owns fan-out.** Cross-repository sweeps and cleanups, status checks, browser
  work, and workspace or cloud-console operations.
- **The primary builder owns depth.** A multi-file change in one repository that needs tests and
  review goes to the [isolated delegated build](../../skills/isolated-workflow/SKILL.md), not to a
  swarm worker.
- **The second vendor is a verifier on request.** The supervised channel and the
  [council](council-review.md) keep a cross-vendor seat for when independent disagreement is
  worth paying for.

### The isolation rule

Workers that edit files never share a checkout. Give each editing worker its own worktree under a
dedicated shadow tree (the [shared policy](../../templates/portable-policy.md) names the
location), or serialize them. A shared workspace is for read-only workers, or for workers the
board runs one at a time. Two writers in one tree produce a diff nobody can attribute and a
verification nobody can trust.

### Handing work back

When a swarm worker discovers that its task is really deep single-repository engineering, it
stops and hands the task to the primary builder in its own session, with a self-contained brief:
the goal, the allowed paths, the acceptance criteria, the verification command, and the
authorization it carries. Grinding a real build out inside a fan-out worker loses the fresh-context
review that the isolated build provides.

## How To Apply It

1. Run [execution-path triage](execution-path-triage.md) first. Native swarm is one engine among
   several; pick it when the signal is many independent workers across repositories, the browser,
   or workspace operations.
2. Write each board task the way you would write a ticket: one outcome, the paths or systems it
   may touch, and what proves it is done.
3. Decide isolation before launch. Read-only tasks can share a workspace. Editing tasks get a
   worktree each, or run serially.
4. Keep the commit with the conductor. Workers report diffs or branches; a human-owned main loop
   reviews and commits.
5. Put the swarm on the same [retirement cadence](execution-path-triage.md#retirement-cadence) as
   every other tool: if a lane goes unused for two weeks after it gets a trigger rule, fix the
   trigger or retire the lane.

## Failure Modes

A swarm used for a deep build trades a reviewed change for a fast one. Editing workers in a shared
workspace overwrite each other silently. A board read is not verification; a worker that says
"done" still needs its evidence checked. Keeping a hand-built worker bus running for work a board
already coordinates pays the supervision tax twice. And a new engine that arrives without a
retirement review of the old one leaves two half-used paths that every agent still has to read.

## Related

- [Execution-path triage](execution-path-triage.md): the engine split and the native-swarm row.
- [Supervised delegation](supervised-delegation.md): the cross-vendor worker channel.
- [Fleet execution](fleet-execution.md): conductor-led rounds across several projects.
- [`isolated-workflow`](../../skills/isolated-workflow/SKILL.md): the default delegated build.
- [One policy, three clients](one-policy-two-engines.md): the rules every engine runs under.
