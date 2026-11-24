#!/usr/bin/env bash
# Minimal assertion helpers for the guard tests.

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  local file="$1"
  local needle="$2"
  [ -f "$file" ] || fail "missing file: $file"
  grep -qF -- "$needle" "$file" || {
    echo "missing '$needle' in $file" >&2
    cat "$file" >&2
    exit 1
  }
}

assert_not_contains() {
  local file="$1"
  local needle="$2"
  [ -f "$file" ] || return 0
  if grep -qF -- "$needle" "$file"; then
    echo "unexpected '$needle' in $file" >&2
    cat "$file" >&2
    exit 1
  fi
}

assert_empty_file() {
  local file="$1"
  [ -f "$file" ] || return 0
  [ ! -s "$file" ] || {
    echo "expected empty file: $file" >&2
    cat "$file" >&2
    exit 1
  }
}

assert_returns_within() {
  local limit="$1"
  shift
  local start end status
  start="$(python3 - <<'PY'
import time
print(time.monotonic())
PY
)"
  set +e
  "$@"
  status=$?
  set -e
  end="$(python3 - <<'PY'
import time
print(time.monotonic())
PY
)"
  python3 - "$limit" "$start" "$end" "$status" <<'PY'
import sys

limit, start, end, status = sys.argv[1:]
elapsed = float(end) - float(start)
if int(status) != 0:
    raise SystemExit(f"command exited {status} after {elapsed:.3f}s")
if elapsed > float(limit):
    raise SystemExit(f"command took {elapsed:.3f}s, limit {limit}s")
PY
}
