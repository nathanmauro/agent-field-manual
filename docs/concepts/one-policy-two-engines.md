# One Policy, Three Clients

## The Problem

Running several coding agents on one machine means a global instruction file, a skill directory,
and a set of hooks per client, and one more place for a rule to drift with every client added.
This setup runs three: a primary builder, a swarm client for fan-out, and a second vendor kept as
an on-request verifier. The rules that matter most (who commits, what needs a gate, where
decisions get recorded) are exactly the ones that quietly diverge, because each client's guide
gets edited in the moment, for the agent that was open.

## The Pattern

Write the operating policy once, in a client-neutral file. A client that supports imports (Claude
Code) imports it directly; a client that does not (Codex, Antigravity) gets it rendered into its
global guide as a managed block with a content hash. Adding a client is one more render target,
not one more policy. A check command fails when a rendered block does not match the source or
when the import is missing or duplicated. Skills follow the same rule: one tracked source
directory per skill, symlinked into each client's skill path, with a small per-client metadata
file where a client needs one. Everything else that is client-specific (keybindings, hooks,
slash-command wrappers) stays in that client's own directory and says so.

```text
agents/
├── shared/
│   ├── portable-policy.md        one source of truth
│   └── skills/<name>/            one source per skill, linked into every client
├── claude/   CLAUDE.md imports the policy; commands/, hooks/, settings
├── codex/    AGENTS.md carries a rendered, hash-marked managed block; prompts/, skills metadata
└── gemini/   GEMINI.md: the same managed block, plus the swarm client's lane rules
```

```text
<!-- BEGIN PORTABLE POLICY source=agents/shared/portable-policy.md sha256=<hash> -->
...rendered policy...
<!-- END PORTABLE POLICY -->
```

### Same rules, different lanes

The rendered policy is identical in every client; what differs is the lane. Each client's own
guide, outside the managed block, says what that client owns and when it hands work to another:
the swarm client owns board-coordinated fan-out and hands deep single-repository builds to the
primary builder in a fresh session; the second vendor runs only when the human names it. Lane
rules live outside the managed block on purpose. They change when usage changes, and the shared
rules (authorship, gates, authorization, continuity) must not churn with them. The routing itself
is in [execution-path triage](execution-path-triage.md#the-engine-split).

## How To Apply It

Start with the policy sections that cause the most damage when they drift: authorship and git
authority, mutation gates, where decisions and handoffs are recorded, and how delegated work
carries authorization. [`templates/portable-policy.md`](../../templates/portable-policy.md) is a
working example. Keep it short enough to live in every session's context; move conditional
mechanics (how to debug the capture system, how to run a fleet) into skills that load on demand.

Give the render step a `check` mode and run it in the repo's test suite, so an edit to the source
that was not rendered, or a hand edit to a rendered block, fails loudly. Install skills by
symlink from one tracked source, never by copying, so an improvement made while working in one
client is immediately live in the others.

Treat the hash check as ring one of a broader downstream audit whenever agent instructions, skills,
hooks, or model defaults change. Before closing a control-plane change, verify every ring that can
drift: rendered policy hashes across every active client (Claude Code, Codex, Gemini), skill
symlinks and registry entries, CLI links on `PATH`, client hook and effort configs, cross-repo
playbooks, the system changelog, and a recorded decision in the durable memory adapter. Audit and
prune unused global MCP servers and zero-use skills on a regular cadence so inactive tools stop
taxing every turn's startup context.

Keep a registry entry for each skill: owner or upstream, pinned provenance, what it can reach
(credentials, external writes, subprocesses), its trigger boundary with positive and negative
examples, validation evidence, last meaningful use, and status. An install is not validation.
[`templates/skill-registry.md`](../../templates/skill-registry.md) has the contract. When a skill
is vendored from someone else's repository, record the upstream commit, the licence, and every
local modification per file, so the next upstream pull is a diff and not an archaeology project.

## Failure Modes

A policy that is pasted rather than rendered is already two policies. A rendered block without a
hash is a paste with better manners. Skills copied into each client fork within a week. A policy
that grows to hold every rule becomes the prompt pile it replaced; conditional mechanics belong in
skills. A registry that records install dates but not uses carries dead prompt surface forever.

Lane rules can drift too. When one client's guide says it owns a lane, the triage doc and the
other clients' guides should agree; a lane two clients both claim, or one nobody claims, is the
same failure as a stale hash.

## Related

- [`templates/portable-policy.md`](../../templates/portable-policy.md)
- [`templates/skill-registry.md`](../../templates/skill-registry.md)
- [Agentic operating system](agentic-operating-system.md)
- [Native swarms](native-swarms.md), the swarm client's lane
- [Kindred systems](../kindred-systems.md), for how vendored upstream skills are tracked
