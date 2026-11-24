#!/usr/bin/env bash
# mcp-result-guard: the PreToolUse Gmail format rewrite and the PostToolUse size guard. Payloads
# are placeholder fixtures shaped like real MCP results; nothing touches the real ~/.claude.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/helpers.sh"

GUARD="$HERE/../mcp-result-guard"
FIXTURES="$HERE/fixtures"
TMP_ROOT="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "${TMP_ROOT%/}/mcp-result-guard.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

SESSION="11111111-2222-3333-4444-555555555555"

# Every test gets a fresh temp HOME, so full copies land under $WORK.
reset_env() {
  export HOME="$WORK/home"
  export PYTHONDONTWRITEBYTECODE=1
  unset MCP_RESULT_GUARD_MAX_CHARS MCP_RESULT_GUARD_KEEP_DAYS MCP_RESULT_GUARD_OFF
  unset CLAUDE_CODE_RESTRICT_PERSONAL_CONFIG

  rm -rf "${WORK:?}/home" "${WORK:?}/out" "${WORK:?}/err"
  mkdir -p "$WORK/home"
}

FULL_DIR() {
  echo "$HOME/.claude/tool-output-full/$SESSION"
}

# Pipe stdin into the hook as Claude Code runs it; stdout to $WORK/out.
run_guard() {
  python3 -I "$GUARD" > "$WORK/out" 2> "$WORK/err"
}

pre_payload() {
  local input="$1"
  jq -c --argjson input "$input" '.tool_input = $input' "$FIXTURES/pre-get-thread.json"
}

# A PostToolUse payload with the given tool_response (JSON) and tool name.
post_payload() {
  local response_file="$1"
  local tool="${2:-mcp__claude_ai_Gmail__search_threads}"
  jq -c \
    --slurpfile response "$response_file" \
    --arg tool "$tool" \
    '.tool_name = $tool | .tool_response = $response[0] | .tool_use_id = "toolu_01PLACEHOLDERPOST00000"' \
    "$FIXTURES/post-get-thread.json"
}

output_field() {
  jq -r "$1" "$WORK/out"
}

pass() {
  echo "ok - $*"
}

test_pre_adds_plain_text_when_missing() {
  reset_env
  pre_payload '{"threadId":"thread0000000001"}' | run_guard

  [ "$(output_field .hookSpecificOutput.hookEventName)" = "PreToolUse" ] || fail "event name"
  [ "$(output_field .hookSpecificOutput.updatedInput.messageFormat)" = "PLAIN_TEXT" ] \
    || fail "messageFormat not set: $(cat "$WORK/out")"
  [ "$(output_field '.hookSpecificOutput | has("permissionDecision")')" = "false" ] \
    || fail "must not touch the permission flow: $(cat "$WORK/out")"
  [ "$(output_field 'keys | join(",")')" = "hookSpecificOutput" ] || fail "extra top-level keys"
  pass "pre: missing messageFormat becomes PLAIN_TEXT, no permissionDecision"
}

test_pre_rewrites_unspecified() {
  reset_env
  pre_payload '{"threadId":"t1","messageFormat":"MESSAGE_FORMAT_UNSPECIFIED"}' | run_guard
  [ "$(output_field .hookSpecificOutput.updatedInput.messageFormat)" = "PLAIN_TEXT" ] \
    || fail "UNSPECIFIED not rewritten: $(cat "$WORK/out")"
  pass "pre: MESSAGE_FORMAT_UNSPECIFIED becomes PLAIN_TEXT"
}

test_pre_leaves_explicit_formats() {
  local format
  for format in FULL_CONTENT MINIMAL METADATA_ONLY PLAIN_TEXT; do
    reset_env
    pre_payload "{\"threadId\":\"t1\",\"messageFormat\":\"$format\"}" | run_guard
    assert_empty_file "$WORK/out"
  done
  pass "pre: explicit FULL_CONTENT, MINIMAL, METADATA_ONLY, PLAIN_TEXT are left alone"
}

test_pre_preserves_other_fields() {
  reset_env
  pre_payload '{"threadId":"t1","extra":{"nested":[1,2]},"flag":true}' | run_guard
  local expected='{"extra":{"nested":[1,2]},"flag":true,"messageFormat":"PLAIN_TEXT","threadId":"t1"}'
  [ "$(jq -cS .hookSpecificOutput.updatedInput "$WORK/out")" = "$expected" ] \
    || fail "fields changed: $(cat "$WORK/out")"
  pass "pre: every other input field is kept"
}

test_pre_ignores_other_tools_and_restricted_config() {
  reset_env
  pre_payload '{"threadId":"t1"}' \
    | jq -c '.tool_name = "mcp__claude_ai_Gmail__search_threads"' \
    | run_guard
  assert_empty_file "$WORK/out"

  pre_payload '{"threadId":"t1"}' | CLAUDE_CODE_RESTRICT_PERSONAL_CONFIG=1 run_guard
  assert_empty_file "$WORK/out"
  pass "pre: other tools untouched; restricted personal config skips the rewrite"
}

test_post_under_threshold_is_silent() {
  reset_env
  printf '%s' '[{"type":"text","text":"small result"}]' > "$WORK/small.json"
  post_payload "$WORK/small.json" | run_guard
  assert_empty_file "$WORK/out"
  [ ! -d "$HOME/.claude/tool-output-full" ] || fail "nothing should be saved under the threshold"
  pass "post: a result under the threshold produces no output and saves nothing"
}

test_post_get_thread_is_trimmed() {
  reset_env
  run_guard < "$FIXTURES/post-get-thread.json"

  local saved
  saved="$(FULL_DIR)/toolu_01PLACEHOLDERGETTHREAD00.json"
  [ "$(output_field .hookSpecificOutput.hookEventName)" = "PostToolUse" ] || fail "event name"
  [ "$(output_field '.hookSpecificOutput.updatedToolOutput | type')" = "string" ] \
    || fail "a string result must come back as a string"
  [ "$(output_field '.hookSpecificOutput | has("updatedMCPToolOutput")')" = "false" ] \
    || fail "only updatedToolOutput is used"

  output_field .hookSpecificOutput.updatedToolOutput > "$WORK/trimmed.txt"
  head -n 1 "$WORK/trimmed.txt" > "$WORK/header.txt"
  assert_contains "$WORK/header.txt" "[mcp-result-guard] trimmed 24154 -> "
  assert_contains "$WORK/header.txt" "full result: $saved (read it with the Read tool"

  tail -n +2 "$WORK/trimmed.txt" > "$WORK/body.json"
  jq -e . "$WORK/body.json" > /dev/null || fail "trimmed body is no longer JSON"
  assert_not_contains "$WORK/body.json" "htmlBody"
  assert_not_contains "$WORK/body.json" "param1="
  # U+034F COMBINING GRAPHEME JOINER, the snippet padding (bash 3.2 has no \u escapes).
  assert_not_contains "$WORK/body.json" "$(printf '\315\217')"
  assert_contains "$WORK/body.json" "https://www.example.com/comm/mypreferences/u/emailunsub?…"
  assert_contains "$WORK/body.json" "https://mail.google.com/mail/u/0/#all/thread0000000001?…"
  assert_contains "$WORK/body.json" "one-sentence placeholder InMail body"

  local size
  size=$(wc -c < "$WORK/trimmed.txt")
  [ "$size" -lt 5000 ] || fail "trimmed result still $size bytes"

  [ -f "$saved" ] || fail "full copy missing: $saved"
  [ "$(stat -c %a "$saved" 2> /dev/null || stat -f %Lp "$saved")" = "600" ] \
    || fail "full copy must be owner-only"
  jq -e --slurpfile fixture "$FIXTURES/post-get-thread.json" \
    '.tool_response == $fixture[0].tool_response
      and .tool_input == $fixture[0].tool_input
      and .tool_name == "mcp__claude_ai_Gmail__get_thread"' "$saved" > /dev/null \
    || fail "saved copy differs from the original"
  pass "post: get_thread trimmed 24K to under 5K, header names the saved full copy"
}

test_post_full_content_keeps_html() {
  reset_env
  jq -c '.tool_input.messageFormat = "FULL_CONTENT"' "$FIXTURES/post-get-thread.json" \
    | MCP_RESULT_GUARD_MAX_CHARS=30000 run_guard
  assert_empty_file "$WORK/out"

  jq -c '.tool_input.messageFormat = "FULL_CONTENT"' "$FIXTURES/post-get-thread.json" | run_guard
  output_field .hookSpecificOutput.updatedToolOutput > "$WORK/trimmed.txt"
  assert_contains "$WORK/trimmed.txt" '"htmlBody":"<html>'
  assert_contains "$WORK/trimmed.txt" "[mcp-result-guard] cut "
  pass "post: an explicit FULL_CONTENT keeps htmlBody and is only cut to the threshold"
}

test_post_blocks_base64_and_image() {
  reset_env
  run_guard < "$FIXTURES/post-blocks-base64.json"

  [ "$(output_field '.hookSpecificOutput.updatedToolOutput | type')" = "array" ] \
    || fail "a block result must come back as blocks"
  [ "$(output_field '.hookSpecificOutput.updatedToolOutput[0].text')" != "null" ] \
    || fail "header block missing"
  output_field '.hookSpecificOutput.updatedToolOutput[0].text' > "$WORK/header.txt"
  assert_contains "$WORK/header.txt" "[mcp-result-guard] trimmed 47534 -> "

  output_field '.hookSpecificOutput.updatedToolOutput[1].text' > "$WORK/body.json"
  jq -e . "$WORK/body.json" > /dev/null || fail "trimmed body is no longer JSON"
  assert_contains "$WORK/body.json" "[image/png base64, "
  assert_contains "$WORK/body.json" "[base64, 1500 chars removed]"
  assert_not_contains "$WORK/body.json" "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"

  jq -e --slurpfile fixture "$FIXTURES/post-blocks-base64.json" \
    '.hookSpecificOutput.updatedToolOutput[2] == $fixture[0].tool_response[1]' "$WORK/out" \
    > /dev/null || fail "image block changed"
  pass "post: base64 and data: blobs replaced, image block passed through untouched"
}

test_post_plain_text_is_cut_at_threshold() {
  reset_env
  python3 -I -c '
import json
line = "plain line of placeholder log output number {:05d} with some words\n"
print(json.dumps("".join(line.format(i) for i in range(1000))))
' > "$WORK/plain.json"
  post_payload "$WORK/plain.json" | MCP_RESULT_GUARD_MAX_CHARS=10000 run_guard

  output_field .hookSpecificOutput.updatedToolOutput > "$WORK/trimmed.txt"
  local chars
  chars=$(python3 -I -c 'import sys; print(len(open(sys.argv[1]).read()))' "$WORK/trimmed.txt")
  [ "$chars" -le 10001 ] || fail "result is $chars chars, over the 10000 threshold"
  [ "$chars" -gt 9000 ] || fail "result cut far below the threshold: $chars"
  assert_contains "$WORK/trimmed.txt" "[mcp-result-guard] cut "
  # The cut lands on a line break, so the last kept log line is whole.
  grep -B1 -F "[mcp-result-guard] cut " "$WORK/trimmed.txt" | head -n 1 > "$WORK/last.txt"
  assert_contains "$WORK/last.txt" "with some words"
  pass "post: non-JSON text cut at a line boundary under the threshold"
}

test_post_markup_kept_outside_mail_tools() {
  reset_env
  python3 -I -c '
import json
rows = "".join("<div class=\"row\"><span>{{ item%d }}</span></div>\n" % i for i in range(700))
content = "<template>\n" + rows + "</template>\n"
print(json.dumps(json.dumps({"path": "App.vue", "content": content})))
' > "$WORK/vue.json"
  post_payload "$WORK/vue.json" mcp__github__get_file_contents | run_guard

  output_field .hookSpecificOutput.updatedToolOutput > "$WORK/trimmed.txt"
  head -n 1 "$WORK/trimmed.txt" > "$WORK/header.txt"
  tail -n +2 "$WORK/trimmed.txt" > "$WORK/body.txt"
  assert_contains "$WORK/body.txt" '<div class=\"row\"><span>{{ item0 }}</span></div>'
  assert_not_contains "$WORK/header.txt" "stripped of tags"
  assert_contains "$WORK/header.txt" "Lossy edits: 1 text cut short."
  assert_contains "$WORK/body.txt" "[mcp-result-guard] cut "
  pass "post: markup outside mail tools is kept, and the header names the cut"
}

test_post_mail_html_text_is_named() {
  reset_env
  python3 -I -c '
import json
rows = "".join("<tr><td>placeholder cell %d</td></tr>" % i for i in range(900))
print(json.dumps("<html><body><table>" + rows + "</table></body></html>"))
' > "$WORK/mail.json"
  post_payload "$WORK/mail.json" mcp__claude_ai_Gmail__get_message | run_guard

  output_field .hookSpecificOutput.updatedToolOutput > "$WORK/trimmed.txt"
  head -n 1 "$WORK/trimmed.txt" > "$WORK/header.txt"
  assert_contains "$WORK/header.txt" "Lossy edits: 1 HTML text stripped of tags"
  assert_not_contains "$WORK/trimmed.txt" "<td>"
  assert_contains "$WORK/trimmed.txt" "placeholder cell 899"
  pass "post: mail HTML is turned into text, and the header says so"
}

test_post_long_whitespace_runs_stay_fast() {
  reset_env
  python3 -I -c '
import json
filler = "͏" + " " * 60000 + "x" + "\t " * 30000 + "<a " * 20000
print(json.dumps([{"type": "text", "text": " " * 100000 + "x" + filler}]))
' > "$WORK/spaces.json"
  post_payload "$WORK/spaces.json" mcp__claude-in-chrome__get_page_text > "$WORK/payload.json"
  assert_returns_within 1 run_guard < "$WORK/payload.json"
  [ "$(output_field '.hookSpecificOutput.updatedToolOutput | type')" = "array" ] \
    || fail "whitespace-heavy result not trimmed: $(head -c 300 "$WORK/out")"
  pass "post: 100K-char whitespace runs and unclosed tags are handled in linear time"
}

test_post_image_only_is_untouched() {
  reset_env
  jq -c '[.tool_response[1], .tool_response[1], .tool_response[1], .tool_response[1],
      .tool_response[1], .tool_response[1], {"type":"text","text":"caption"}]' \
    "$FIXTURES/post-blocks-base64.json" > "$WORK/images.json"
  post_payload "$WORK/images.json" mcp__claude-in-chrome__computer | run_guard
  assert_empty_file "$WORK/out"
  pass "post: image data never counts toward the threshold"
}

test_malformed_payloads_fail_open() {
  reset_env
  local payload
  for payload in 'not json' '[1,2,3]' '{}' '{"hook_event_name":"PostToolUse"}' \
    '{"hook_event_name":"PostToolUse","tool_name":"mcp__x__y","tool_response":{"a":1}}' \
    '{"hook_event_name":"PreToolUse","tool_name":"mcp__claude_ai_Gmail__get_thread","tool_input":"x"}'; do
    printf '%s' "$payload" | run_guard || fail "non-zero exit for: $payload"
    assert_empty_file "$WORK/out"
  done
  pass "malformed or unknown payloads exit 0 with no output"
}

test_off_switch_and_bad_save_dir() {
  reset_env
  MCP_RESULT_GUARD_OFF=1 run_guard < "$FIXTURES/post-get-thread.json"
  assert_empty_file "$WORK/out"

  # A save that cannot happen must not trim: the header would point at nothing.
  mkdir -p "$HOME/.claude"
  : > "$HOME/.claude/tool-output-full"
  run_guard < "$FIXTURES/post-get-thread.json" || fail "non-zero exit on save failure"
  assert_empty_file "$WORK/out"
  pass "MCP_RESULT_GUARD_OFF disables it; a failed save leaves the result alone"
}

test_old_copies_are_cleaned() {
  reset_env
  local old_dir="$HOME/.claude/tool-output-full/old-session"
  mkdir -p "$old_dir" "$(FULL_DIR)"
  : > "$old_dir/toolu_old.json"
  : > "$(FULL_DIR)/toolu_recent.json"
  touch -t 202601010000 "$old_dir/toolu_old.json"

  run_guard < "$FIXTURES/post-get-thread.json"
  [ ! -e "$old_dir" ] || fail "a copy older than 14 days should be gone with its empty dir"
  [ -f "$(FULL_DIR)/toolu_recent.json" ] || fail "a recent copy must stay"

  : > "$(FULL_DIR)/toolu_old2.json"
  touch -t 202601010000 "$(FULL_DIR)/toolu_old2.json"
  MCP_RESULT_GUARD_KEEP_DAYS=0 run_guard < "$FIXTURES/post-get-thread.json"
  [ -f "$(FULL_DIR)/toolu_old2.json" ] || fail "KEEP_DAYS=0 keeps everything"
  pass "copies older than KEEP_DAYS are deleted on each save; 0 keeps them"
}

test_runs_fast() {
  reset_env
  assert_returns_within 1 python3 -I "$GUARD" < "$FIXTURES/post-get-thread.json" > /dev/null
  pass "post: a 24K result is handled well inside the 5 s hook timeout"
}

test_pre_adds_plain_text_when_missing
test_pre_rewrites_unspecified
test_pre_leaves_explicit_formats
test_pre_preserves_other_fields
test_pre_ignores_other_tools_and_restricted_config
# Gmail view links carry the thread and message ids in the fragment, after a long query.
test_post_url_fragment_survives() {
  reset_env
  local url="https://mail.google.com/mail/?authuser=someone%40example.com&pad=$(printf 'x%.0s' $(seq 1 120))#all/thread-f:111|msg-f:222"
  local body
  body="$(python3 -I -c 'import json,sys; print(json.dumps(json.dumps({"viewUrl": sys.argv[1], "pad": "y" * 30000})))' "$url")"
  printf '%s' "$body" > "$WORK/resp.json"
  post_payload "$WORK/resp.json" | run_guard

  output_field '.hookSpecificOutput.updatedToolOutput' | grep -q '?…#all/thread-f:111|msg-f:222' \
    || fail "URL fragment was dropped with the query"
  pass "post: a long URL keeps its fragment when the query is removed"
}

test_post_under_threshold_is_silent
test_post_get_thread_is_trimmed
test_post_full_content_keeps_html
test_post_blocks_base64_and_image
test_post_plain_text_is_cut_at_threshold
test_post_markup_kept_outside_mail_tools
test_post_mail_html_text_is_named
test_post_long_whitespace_runs_stay_fast
test_post_image_only_is_untouched
test_malformed_payloads_fail_open
test_off_switch_and_bad_save_dir
test_old_copies_are_cleaned
test_runs_fast
test_post_url_fragment_survives

echo "mcp-result-guard: all tests passed"
