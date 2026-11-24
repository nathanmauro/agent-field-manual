# tools

Small command-line tools that replace the read calls of the claude.ai connectors (Gmail,
Calendar, Drive, Docs, Sheets, Linear). Claude Code and Codex both call them through the shell;
Codex can't use claude.ai connectors at all, so a CLI is the one thing both agents share.

## Why

A connector returns the API's whole object: HTML bodies, every field, full descriptions on list
calls. Measured on the same requests:

| request                                  | connector (chars) | tool (chars) |
| ---------------------------------------- | ----------------: | -----------: |
| one Gmail thread, a one-sentence message |            35,887 |        1,688 |
| a week of calendar, 12 events            |            14,085 |          802 |
| 25 Linear issues                         |            35,216 |        2,952 |

## Tools

| tool                               | replaces                                                        | backend                           |
| ---------------------------------- | --------------------------------------------------------------- | --------------------------------- |
| `gmail-thread <id>`                | Gmail `get_thread`, `get_message`                               | `gws`                             |
| `gcal-agenda [--days N] [-q text]` | Calendar `list_events`, `search_events`, `get_event`            | `gws`                             |
| `gdrive-find <words>`              | Drive `search_files`                                            | `gws`                             |
| `gdrive-read <id or url>`          | Drive `read_file_content`, Docs `read_doc`, Sheets `get_values` | `gws`                             |
| `linear-find [filters]`            | Linear `list_issues`                                            | Linear GraphQL, `$LINEAR_API_KEY` |

`gws` is a Google Workspace CLI; install and authenticate it first. Put this directory on your
`PATH`, or symlink the tools into `~/.local/bin` (the `lib/` folder has to stay next to them).

Writes are left out on purpose. Every expensive call measured was a read, and a write through the
shell loses the connector's per-tool permission prompt; a retry can also send twice. `gws` has
lean write forms already (`gws gmail +send`, `+reply`, `+forward`, each with `--draft`).

## Contract

Every tool is shaped by `lib/lean.py`:

- Text by default: one record per line, the columns an agent reads, `0 results` when empty.
- `--json`: NDJSON, one flat object per line, empty values omitted.
- `--fields a,b,c`: picks and orders columns from the tool's closed set. An unknown name exits 2
  and lists the valid ones.
- `--max-chars N`: caps every free-text field. Cuts are marked with `…`, and "more" notes
  (`# 20 shown, more: --limit 40`) go to stderr, so an agent still sees them and a script's
  `$(...)` stays pure data.
- Metrics: with `$TOOL_METRICS` set to a file path, each run writes the raw API bytes it
  processed there, so a wrapper can count what was saved.

Header lines in each tool: `# desc:`, `# usage:`, `# effect: read`, and `# replaces:` (the MCP
tool names it stands in for).

## From scripts and code mode

The tools are plain executables, so a shell loop or a code-mode script calls them and keeps only
what it needs:

```bash
gdrive-find budget --json --fields id,name | head -3
gcal-agenda --days 7 --fields start,title
```

## Which connectors to replace

`mcp-inventory` joins `../scripts/tool-stats/tools.json`, your map of what each MCP tool is for
(`mcp-equivalents.json`; start from `mcp-equivalents.sample.json`), and each tool's
`# replaces:` header, then ranks every MCP tool by tokens with its lean replacement:

```bash
python3 -I ../scripts/tool-stats/tools.py
cp mcp-equivalents.sample.json mcp-equivalents.json   # then edit
./mcp-inventory              # top 40 as a table
./mcp-inventory --markdown   # a report
```

On the machine this came from: 38% of MCP result tokens are now covered by these tools, 21% was
a self-written server to fix at the source, 15% had an existing CLI or was retired, and 25%
stays (browser and screenshot tools need to see the screen).

## Tests

`bash tests/test.sh` runs offline against a fake `gws` and synthetic fixtures. No test touches a
real account.
