# tool-stats

Sizes every tool result in your Claude Code and Codex transcripts and shows the totals on a local
page: tokens per tool and per server, the heaviest calls, and flags for calls whose result was
heavier than the tool's default format (Gmail returning `htmlBody` when no format was set).

```sh
python3 -I tools.py     # scans ~/.claude/projects and ~/.codex/sessions, writes tools.json
python3 -I serve.py     # http://127.0.0.1:8742/
```

Claude Code token sizes come from the input-size growth between requests, split across the
results that arrived in between; where that can't be trusted, and for all Codex results, sizes
are estimated as characters / 3.

Everything stays on your machine. `tools.json` holds snippets of your tool inputs and results, so
it's gitignored; don't share it. The Library tab reads any CLI named in `$TOOL_LIBRARY_CLI` that
answers `stats --json` and `list`. See [Context budget](../../docs/concepts/context-budget.md).
