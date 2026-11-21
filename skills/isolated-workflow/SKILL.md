---
name: isolated-workflow
description: >
  Default delegated build. The conductor runs a background workflow in which a fresh-context
  implementer builds a scoped change, separate-context reviewers verify it cold, and a fix agent
  repairs only real issues, all inside one model family. Use for "delegated build", "isolated
  build", "hand this to a worker", "build this in the background", or a multi-file ticket from
  execution-path triage. Use codex-workflow-hybrid instead only when cross-vendor verification is
  explicitly requested.
---

> Public adaptation note: This skill is a sanitized public version of a private local workflow.
> The template targets Claude Code's Workflow tool; set the model tiers through your own runtime,
> and replace the notification channel, issue tracker, and memory adapter with your own.
>
> Default since 2026-11-16: delegated builds run isolated same-vendor implementer and reviewer
> contexts. The cross-vendor [`codex-workflow-hybrid`](../codex-workflow-hybrid/SKILL.md) stays
> available as an explicit opt-in.

# Isolated workflow (the conductor orchestrates, a fresh agent implements, others verify)

This keeps what the cross-vendor hybrid bought: the implementer works in its own context, the
reviewers never saw the implementer's reasoning, and the main loop owns git. It drops only the
second vendor. What replaces the outside reader is a stateless adversarial oracle the main loop
can query on demand.

## Why this shape

- **A fresh implementer writes the code.** One workflow agent gets the scoped task and nothing
  else, so the conductor's context stays clean and the implementer cannot lean on it.
- **Separate reviewers verify.** Each lens is its own agent that reads the diff cold. When the
  change is judgment-heavy, put the reviewers on a different tier from the implementer (the
  judgment tier builds, the fast tier checks mechanics, a judgment-tier lens checks honesty and
  correctness).
- **An adversarial oracle on demand.** For a design fork or a high-stakes diff, the main loop
  sends the diff to a stateless, zero-tool specialist model with a red-team or architecture
  prompt. It has no tools and no session history, so it reads the change the way an outsider
  would. See [Execution-path triage](../../docs/concepts/execution-path-triage.md).
- **The main loop owns git.** Agents never `git add`, commit, or push. The main loop commits with
  the human owner as sole author.

## Model routing (set the model on every agent; never inherit the session model)

| Stage                                 | Tier     | Effort   |
| ------------------------------------- | -------- | -------- |
| Implement or Fix, judgment-heavy code | judgment | `high`   |
| Implement or Fix, mechanical edits    | fast     | `medium` |
| Functional lens (scope, build, tests) | fast     | `medium` |
| Editorial lens (honesty, correctness) | judgment | `medium` |

The oracle never runs inside the workflow. It is a single stateless call from the main loop.

## Isolation

- **Default:** one implementer edits the checkout directly, sequentially, and the reviewers read
  the same tree. This is the simplest shape and the one the template uses.
- **Parallel implementers** each get their own worktree (in Claude Code, `isolation: 'worktree'`
  on the agent). Two editing agents never share a checkout. The main loop reviews and merges each
  worktree's branch.
- Worktrees live in a dedicated shadow tree, not beside the real repositories; see the worktree
  rule in the [shared policy](../../templates/portable-policy.md).

## Run it: three movements

### 1. Pre-flight (main loop; do not delegate)

1. **Lock the scope:** what this phase delivers, what it defers, the allowed paths, and the
   verification command. Show the human owner the shape when it is not already settled.
2. **Persist the plan** off-session when the build spans sessions: an issue-tracker ticket, a
   repo plan doc, or a decision in the durable memory adapter. A ticket produced by the spec stage
   is already the plan.
3. **Branch** only when the repo's guide says so.
4. **Quote the authorization** the work rests on into the template, so every agent can tell the
   approved task from anything the conductor adds. See
   [Authorization provenance](../../docs/concepts/authorization-provenance.md).

### 2. Execute (the workflow runtime)

Customize [`templates/workflow.js`](templates/workflow.js) and pass it to the workflow runtime.

- **Implement.** One agent per cohesive unit. Run units sequentially when they share files. Any
  outcome other than `done` stops the workflow and returns to the main loop instead of verifying
  a half-written tree.
- **Verify.** Parallel lenses with a structured schema, `functional` and `editorial`.
- **Gate.** A missing or malformed reviewer verdict stops the workflow; it never counts as a
  clean review.
- **Fix.** One conditional agent, given the original task plus only the issues marked
  `real: true`. Agents are stateless, so restate the task; do not assume it remembers.

### 3. Post-flight (main loop)

1. For a running app or server, rebuild, restart, and confirm the new behavior through real use.
2. Optional: send the diff to the stateless oracle with a red-team prompt for a high-stakes
   change.
3. Review the diff scope, re-run the proving command yourself, and commit exact paths with the
   human owner as sole author and no model trailer.
4. Report the diff, the verdicts, and anything left for the human owner.

## Hard guardrails (put these in every implementer prompt)

- Edit only the named paths. Never touch out-of-scope layers.
- Never run `git add`, `git commit`, or `git push`. Leave changes unstaged.
- No new runtime dependencies. Match the existing code style.
- If the spec does not decide a fork, return `question` instead of guessing.
- Return a terse summary of files changed, not reasoning traces.

## Stall rule

A workflow agent that has not finished after about 45 minutes, or shows no tool activity for
10, gets reported: notify the human owner through the configured channel, inspect, then decide.
Never say "it's building" and stop unless the workflow's completion notification is armed.

## Compared with the hybrid

| Property              | Isolated workflow           | Hybrid                                  |
| --------------------- | --------------------------- | --------------------------------------- |
| Implementer context   | Fresh, same vendor          | Fresh, other vendor over a channel      |
| Reviewer independence | Separate contexts           | Separate contexts and a different model |
| Outside reader        | Stateless oracle, on demand | The second vendor, on every change      |
| Moving parts          | One runtime                 | Runtime plus a supervised channel       |
| Who owns git          | Main loop                   | Main loop (sandbox enforced)            |
