#!/usr/bin/env bash
# enforce_agent_onboarding_gate.sh — the single command an agent runs before work.
#
# It composes the repository's three gates and refuses on the first failure:
#   1. governance consistency  — instructions agree, no private data, target sane
#   2. negative test coverage  — every mechanism proves what it refuses
#   3. the test suite itself   — the mechanisms actually behave that way
#
# "It passed last time" is not accepted as evidence. The gate re-runs in full on
# every invocation; a marker file is written afterwards for observability only,
# and is never consulted as a shortcut. A marker that could skip the gate would
# make the gate optional, which is the same as not having one.
#
# Exit: 0 = cleared to work · 1 = blocked. Read-only apart from the marker file.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONSISTENCY_GATE="$ROOT_DIR/ops/check_release_governance_consistency.sh"
COVERAGE_GATE="$ROOT_DIR/ops/check_negative_test_coverage.py"

fail() {
  echo "[FAIL] $1" >&2
  [[ -n "${2:-}" ]] && echo "FIX: $2" >&2
  exit 1
}

PYTHON_BIN="$(command -v python3 || true)"
[[ -n "$PYTHON_BIN" ]] || fail "python3 not found on PATH" "install Python 3.10 or newer"

[[ -x "$CONSISTENCY_GATE" ]] || fail "consistency gate missing or not executable: $CONSISTENCY_GATE" "chmod +x $CONSISTENCY_GATE"
[[ -f "$COVERAGE_GATE" ]] || fail "negative test coverage gate missing: $COVERAGE_GATE" "restore ops/check_negative_test_coverage.py"

echo "── gate 1/3: governance consistency ──"
"$CONSISTENCY_GATE" || fail "governance consistency check failed" "read the FAIL lines above; each names the file and the missing pattern"

echo
echo "── gate 2/3: negative test coverage ──"
"$PYTHON_BIN" "$COVERAGE_GATE" || fail "negative test coverage gate failed" "add a test asserting what the named mechanism refuses"

echo
echo "── gate 3/3: test suite ──"
# Discovery is run from the repository root with a relative start directory:
# tests/ is intentionally not a package, and an absolute start directory would
# make unittest demand one.
( cd "$ROOT_DIR" && "$PYTHON_BIN" -m unittest discover tests/ ) \
  || fail "test suite is red" "a red suite cannot clear the onboarding gate"

# Observability marker. Written after the fact, never read as a shortcut.
MARKER_DIR="$(git -C "$ROOT_DIR" rev-parse --git-dir 2>/dev/null || true)"
if [[ -n "$MARKER_DIR" ]]; then
  if ! date -u +"%Y-%m-%dT%H:%M:%SZ passed" > "$MARKER_DIR/AGENT_ONBOARDING_PASSED" 2>/dev/null; then
    echo "[WARN] could not write the observability marker under $MARKER_DIR" >&2
    echo "FIX: harmless — a sandboxed .git blocks the write; the gate checks above still ran and passed." >&2
  fi
fi

echo
echo "[PASS] AGENT_ONBOARDING_GATE_PASSED"
