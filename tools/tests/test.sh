#!/usr/bin/env bash
# Offline checks for the lean connector tools: a fake gws and a file:// Linear endpoint serve
# synthetic fixtures, so no test touches a real account.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$HERE/.."
FIX="$HERE/fixtures"
TMPDIR="$(mktemp -d -t lean-tools.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

source "$HERE/../../hooks/mcp-result-guard/tests/helpers.sh"

mkdir -p "$TMPDIR/bin"

cat > "$TMPDIR/bin/gws" << SH
#!/usr/bin/env bash
echo "\$*" >> "$TMPDIR/gws.log"
case "\$1 \$2 \$3" in
  "gmail users threads") cat "$FIX/gmail-thread.json" ;;
  "calendar events list") cat "$FIX/calendar-events.json" ;;
  "drive files list") cat "$FIX/drive-files.json" ;;
  *) echo "unexpected: \$*" >&2; exit 9 ;;
esac
SH
chmod +x "$TMPDIR/bin/gws"

export PATH="$TMPDIR/bin:$PATH"
export LINEAR_API_URL="file://$FIX/linear-issues.json"
export LINEAR_API_KEY="test-key"
export MCP_EQUIVALENTS_JSON="$TOOLS/mcp-equivalents.sample.json"

OUT="$TMPDIR/out"
ERR="$TMPDIR/err"

# --- gmail-thread: HTML-only bodies become text, quoted history and tracking queries go.
METRICS="$TMPDIR/metrics"
TOOL_METRICS="$METRICS" "$TOOLS/gmail-thread" t1 > "$OUT"

assert_contains "$OUT" "Alex <alex@example.com>  Role question"
assert_contains "$OUT" "Quick question about the role."
assert_contains "$OUT" "Notes: www.example.com/comm/in/someone"
assert_contains "$OUT" "profile"
assert_contains "$OUT" "Sounds good, Tuesday works."
assert_not_contains "$OUT" "<p>"
assert_not_contains "$OUT" "color:red"
assert_not_contains "$OUT" "trk=abc"
assert_not_contains "$OUT" "wrote:"
[ "$(cat "$METRICS")" -gt 0 ] || fail "gmail-thread did not report raw bytes"

"$TOOLS/gmail-thread" t1 --json --no-body --fields from,labels > "$OUT"
[ "$(wc -l < "$OUT")" -eq 2 ] || fail "gmail-thread --json should be one line per message"
python3 -c 'import json,sys; [json.loads(l) for l in open(sys.argv[1])]' "$OUT"
assert_contains "$OUT" '"labels": ["SENT"]'
assert_not_contains "$OUT" '"body"'

"$TOOLS/gmail-thread" t1 --last 1 --max-chars 10 > "$OUT"
assert_contains "$OUT" "Sounds go…"
assert_not_contains "$OUT" "Alex <alex@example.com>"

# --- gcal-agenda: sorted by start, all-day events keep their date, meet links show as "meet".
"$TOOLS/gcal-agenda" --from 2026-11-24 --to 2026-11-28 > "$OUT"
head -1 "$OUT" | grep -q "Standup" || fail "gcal-agenda not sorted by start"
assert_contains "$OUT" "Piano  meet"
assert_contains "$OUT" "2026-11-26  2026-11-27  Holiday"
assert_contains "$TMPDIR/gws.log" '"singleEvents": true'

"$TOOLS/gcal-agenda" --days 3 --json --fields title,attendees,desc > "$OUT"
assert_contains "$OUT" '{"title": "Piano", "attendees": 2, "desc": "bring music"}'

"$TOOLS/gcal-agenda" --days 3 --limit 1 > "$OUT" 2> "$ERR"
[ "$(wc -l < "$OUT")" -eq 1 ] || fail "gcal-agenda --limit ignored"
assert_contains "$ERR" "# 1 shown, more:"

# --- gdrive-find: query building, kind mapping, the "more" note stays off stdout.
"$TOOLS/gdrive-find" plan "it's" --kind doc > "$OUT" 2> "$ERR"
assert_contains "$OUT" "f1  doc  2026-11-20  Plan doc"
assert_contains "$OUT" "f2  sheet"
assert_contains "$ERR" "more: --limit 40"
assert_not_contains "$OUT" "more:"
assert_contains "$TMPDIR/gws.log" "name contains 'it\\\\'s'"
assert_contains "$TMPDIR/gws.log" "mimeType = 'application/vnd.google-apps.document'"

if "$TOOLS/gdrive-find" > "$OUT" 2> "$ERR"; then
  fail "gdrive-find with no query should fail"
fi
assert_contains "$ERR" "give search words"

# --- linear-find: the closed field set and the empty marker.
"$TOOLS/linear-find" --state open > "$OUT"
assert_contains "$OUT" "ABC-1  Todo  High  First issue  Demo"

"$TOOLS/linear-find" --json --fields id,assignee,labels > "$OUT"
assert_contains "$OUT" '{"id": "ABC-1", "labels": ["bug"]}'

set +e
"$TOOLS/linear-find" --fields id,nope > "$OUT" 2> "$ERR"
status=$?
set -e
[ "$status" -eq 2 ] || fail "unknown field should exit 2, got $status"
assert_contains "$ERR" "valid: id,state"

# --- every tool carries the library headers.
for tool in gmail-thread gcal-agenda gdrive-find gdrive-read linear-find; do
  sed -n 2p "$TOOLS/$tool" | grep -q '^# desc: ' || fail "$tool: line 2 must be # desc:"
  grep -q '^# effect: read$' "$TOOLS/$tool" || fail "$tool: missing # effect: read"
  grep -q '^# replaces: mcp__' "$TOOLS/$tool" || fail "$tool: missing # replaces:"
  python3 -c "import ast, sys; ast.parse(open(sys.argv[1]).read())" "$TOOLS/$tool"
done

# --- mcp-inventory: aliases fold, # replaces: wins, previews never leave the stats file.
INV="$TOOLS/mcp-inventory"

"$INV" --stats "$FIX/tools-stats.json" --json > "$OUT"
python3 - "$OUT" << 'PY'
import json, sys

report = json.load(open(sys.argv[1]))
rows = {(r["server"], r["tool"]): r for r in report["tools"]}

thread = rows[("claude.ai Gmail", "get_thread")]
assert thread["calls"] == 4 and thread["tokens"] == 1500, thread
assert thread["verdict"] == "replaced" and thread["lean"] == "gmail-thread", thread
assert rows[("memory", "recallContext")]["verdict"] == "fix-source"
assert rows[("mystery", "thing")]["verdict"] == "unmapped"
assert report["mcp_tokens"] == 2000 and report["mcp_share_of_all"] == 0.2, report
assert all(r["server"] != "claude code built-in" for r in report["tools"])
PY
assert_not_contains "$OUT" "PRIVATE-PREVIEW"

"$INV" --stats "$FIX/tools-stats.json" --markdown > "$OUT"
assert_contains "$OUT" "GENERATED by tools/mcp-inventory"
assert_contains "$OUT" "| 1,500 | 4 |"
assert_not_contains "$OUT" "PRIVATE-PREVIEW"

echo "lean-tools: ok"
