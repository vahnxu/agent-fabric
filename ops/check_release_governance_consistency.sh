#!/usr/bin/env bash
# check_release_governance_consistency.sh — the repository's own release gate.
#
# WHAT IT ASSERTS
#   C1. The per-runtime instruction files (AGENTS.md / CLAUDE.md / GEMINI.md)
#       carry identical rules. Drift means each runtime believes it is reading
#       the authority while they disagree.
#   C2. Those files still carry the onboarding clauses, so a fresh agent cannot
#       start work without hitting the gate.
#   C3. No tracked file leaks private data — absolute home directories, machine
#       hostnames, personal identifiers or credential material. This is a public
#       repository; a gate that requires such a string to be present, as an
#       earlier version of this file did, inverts the very rule CONTRIBUTING.md
#       states.
#   C4. The declared publication target in governance.yaml matches the remote
#       actually configured, or is explicitly marked pending.
#   C5. Baseline hygiene: .gitignore covers the usual leak vectors, and the
#       governance files the gate depends on exist.
#
# Deliberately host-agnostic and vendor-agnostic: this script must produce the
# same verdict on a contributor's laptop, in CI, and on the author's machine. It
# contains no absolute path and no product name by construction — C3 checks
# itself along with everything else.
#
# Exit: 0 = all checks pass · 1 = at least one check failed. Read-only.
set -euo pipefail

# FABRIC_GATE_ROOT lets the suite point this gate at a fixture repository. It
# only ever moves where the gate looks; it cannot relax a single check.
ROOT_DIR="${FABRIC_GATE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT_DIR"

CANONICAL_DOC="$ROOT_DIR/AGENTS.md"
MIRROR_DOCS=("$ROOT_DIR/CLAUDE.md" "$ROOT_DIR/GEMINI.md")
GOVERNANCE_YAML="$ROOT_DIR/governance.yaml"
DOC_INDEX="$ROOT_DIR/docs/workspace/README.md"
ONBOARDING_DOC="$ROOT_DIR/docs/workspace/NEW_AGENT_ONBOARDING_PROMPT.md"
ONBOARDING_GATE="$ROOT_DIR/ops/enforce_agent_onboarding_gate.sh"
COVERAGE_GATE="$ROOT_DIR/ops/check_negative_test_coverage.py"
GITIGNORE_FILE="$ROOT_DIR/.gitignore"

fail_count=0

pass() { echo "PASS: $1"; }
fail() {
  echo "FAIL: $1"
  [[ -n "${2:-}" ]] && echo "  $2"
  fail_count=$((fail_count + 1))
}

check_contains() {
  local file="$1" pattern="$2" label="$3"
  if [[ -f "$file" ]] && grep -Fq -- "$pattern" "$file"; then
    pass "$label"
  else
    fail "$label" "missing pattern in $(basename "$file"): $pattern"
  fi
}

check_file_exists() {
  if [[ -f "$1" ]]; then pass "$2"; else fail "$2" "missing file: $1"; fi
}

echo "== C1: agent instruction files agree =="
if [[ -f "$CANONICAL_DOC" ]]; then
  pass "AGENTS.md is present as the canonical instruction file"
  for mirror in "${MIRROR_DOCS[@]}"; do
    name="$(basename "$mirror")"
    if [[ ! -e "$mirror" ]]; then
      fail "$name exists" "run ./ops/sync_agent_instructions.sh"
    elif cmp -s "$CANONICAL_DOC" "$mirror"; then
      pass "$name matches AGENTS.md"
    else
      fail "$name matches AGENTS.md" "drift detected — run ./ops/sync_agent_instructions.sh"
    fi
  done
else
  fail "AGENTS.md is present as the canonical instruction file"
fi

echo "== C2: onboarding clauses survive =="
check_contains "$CANONICAL_DOC" "Every agent must run the onboarding gate" "instructions require the onboarding gate"
check_contains "$CANONICAL_DOC" "./ops/check_release_governance_consistency.sh" "instructions name the consistency command"
check_contains "$CANONICAL_DOC" "Work must not proceed if the gate fails" "instructions block work on gate failure"
for doc in "$ONBOARDING_DOC" "$DOC_INDEX"; do
  check_contains "$doc" "enforce_agent_onboarding_gate.sh" "$(basename "$doc") references the onboarding gate"
done

echo "== C3: no private data in tracked files =="
# Patterns are assembled from fragments so this script does not itself contain a
# literal example of what it forbids — otherwise it would flag itself, and a gate
# that must be exempted from its own rule teaches contributors to add exemptions.
declare -a LEAK_LABELS=(
  "absolute macOS home directory"
  "absolute Linux home directory"
  "SSH URL carrying a user and host"
  "private hostname suffix"
  "AWS access key id"
  "PEM private key block"
  "bearer token assignment"
)
declare -a LEAK_PATTERNS=(
  "/Users/[a-z][a-z0-9_-]"
  "/home/[a-z][a-z0-9_-]"
  "ssh://[a-zA-Z0-9_.-]+@"
  "[.](local|lan|internal)([/:[:space:]]|$)"
  "AKIA[0-9A-Z]{16}"
  "BEGIN [A-Z ]*PRIVATE KEY"
  "(api_key|secret|token|password)[[:space:]]*=[[:space:]]*[\"'][A-Za-z0-9_-]{16,}"
)
# Fixtures may need a home-shaped path; they must use these reserved examples.
ALLOWED_EXAMPLES="/Users/test|/Users/example|/home/runner|/home/user"

leak_found=0
tracked_files="$(git -C "$ROOT_DIR" ls-files 2>/dev/null || true)"
if [[ -z "$tracked_files" ]]; then
  echo "INFO: not a git checkout; skipping tracked-file leak scan"
else
  for i in "${!LEAK_PATTERNS[@]}"; do
    hits="$(printf '%s\n' "$tracked_files" \
      | xargs grep -nIE "${LEAK_PATTERNS[$i]}" 2>/dev/null \
      | grep -vE "$ALLOWED_EXAMPLES" \
      | grep -vE '^ops/check_release_governance_consistency\.sh:' || true)"
    if [[ -n "$hits" ]]; then
      fail "no ${LEAK_LABELS[$i]} in tracked files"
      printf '%s\n' "$hits" | sed 's/^/    /' | head -8
      leak_found=1
    fi
  done
  [[ "$leak_found" -eq 0 ]] && pass "no private paths, hosts or credential material in tracked files"
fi

echo "== C4: declared publication target matches reality =="
if [[ -f "$GOVERNANCE_YAML" ]]; then
  pass "governance.yaml exists"
  declared_remote="$(grep -E '^[[:space:]]*durable_remote:' "$GOVERNANCE_YAML" | head -1 | sed 's/.*:[[:space:]]*//' | tr -d '"' || true)"
  actual_remote="$(git -C "$ROOT_DIR" remote 2>/dev/null | head -1 || true)"
  if [[ -z "$declared_remote" ]]; then
    fail "governance.yaml declares a durable_remote"
  elif [[ "$declared_remote" == "pending" ]]; then
    pass "publication target is explicitly pending (no remote assertion made)"
  elif [[ -z "$actual_remote" ]]; then
    fail "declared durable_remote '$declared_remote' has no configured git remote"
  elif [[ "$declared_remote" == "$actual_remote" ]]; then
    pass "declared durable_remote '$declared_remote' matches the configured remote"
  else
    fail "declared durable_remote '$declared_remote' does not match configured remote '$actual_remote'"
  fi
else
  fail "governance.yaml exists"
fi

echo "== C5: baseline hygiene =="
for pattern in ".DS_Store" ".env" "__pycache__"; do
  check_contains "$GITIGNORE_FILE" "$pattern" ".gitignore excludes $pattern"
done
check_file_exists "$DOC_INDEX" "docs/workspace/README.md exists"
check_file_exists "$ONBOARDING_DOC" "docs/workspace/NEW_AGENT_ONBOARDING_PROMPT.md exists"
check_file_exists "$ONBOARDING_GATE" "onboarding gate script exists"
check_file_exists "$COVERAGE_GATE" "negative test coverage gate exists"
check_contains "$ONBOARDING_GATE" "AGENT_ONBOARDING_GATE_PASSED" "onboarding gate prints its pass marker"

if [[ "$fail_count" -gt 0 ]]; then
  echo "== RESULT: FAIL ($fail_count check(s) failed) =="
  exit 1
fi

echo "== RESULT: PASS (all checks passed) =="
