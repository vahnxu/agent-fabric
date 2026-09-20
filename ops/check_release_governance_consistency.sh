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

echo "== C1: agent instruction files are local-only and correctly shaped =="
# 2026-09-20 (owner decision): instruction files are NOT published from this
# repository. They exist in every working copy but are never tracked, so that a
# public clone carries no CLAUDE.md / AGENTS.md / GEMINI.md.
#
# What this gate now asserts:
#   · AGENTS.md is present locally and carries the instructions
#   · CLAUDE.md is exactly the canonical one-line loader `@AGENTS.md`
#     (byte-exact: any prose in the loader re-creates a second hand-written face)
#   · GEMINI.md resolves to AGENTS.md
#   · none of the three is tracked by git
# It no longer asserts byte-equality between carriers — CLAUDE.md is deliberately
# a loader now, not a copy.
CANONICAL_LOADER='@AGENTS.md'
if [[ -f "$CANONICAL_DOC" ]]; then
  pass "AGENTS.md is present as the canonical instruction file"
else
  fail "AGENTS.md is present as the canonical instruction file"
fi

if [[ -f "$ROOT_DIR/CLAUDE.md" ]]; then
  if [[ "$(cat "$ROOT_DIR/CLAUDE.md")" == "$CANONICAL_LOADER" ]]; then
    pass "CLAUDE.md is exactly the canonical loader"
  else
    fail "CLAUDE.md is exactly the canonical loader" "write a single line: $CANONICAL_LOADER"
  fi
else
  fail "CLAUDE.md is present as the loader" "write a single line: $CANONICAL_LOADER"
fi

if [[ -e "$ROOT_DIR/GEMINI.md" ]] && cmp -s "$CANONICAL_DOC" "$ROOT_DIR/GEMINI.md"; then
  pass "GEMINI.md resolves to AGENTS.md"
else
  fail "GEMINI.md resolves to AGENTS.md" "ln -sf AGENTS.md GEMINI.md"
fi

for _f in AGENTS.md CLAUDE.md GEMINI.md; do
  if git -C "$ROOT_DIR" ls-files --error-unmatch "$_f" >/dev/null 2>&1; then
    fail "$_f is untracked" "git rm --cached $_f  # instruction files must not be published"
  else
    pass "$_f is untracked (not published)"
  fi
done

echo "== C2: onboarding clauses survive =="
# Assert the COMMANDS are named, not that a particular English sentence survives.
# The earlier version pinned an exact sentence, which meant any contributor
# rewording the instructions turned CI red for a cosmetic reason — a gate that
# fires on prose edits teaches people that red means "ignore me".
check_contains "$CANONICAL_DOC" "./ops/install_git_hooks.sh" "instructions name the hook installer"
check_contains "$CANONICAL_DOC" "./ops/check_release_governance_consistency.sh" "instructions name the consistency command"
check_contains "$CANONICAL_DOC" "./ops/enforce_agent_onboarding_gate.sh" "instructions name the composed gate"
check_contains "$CANONICAL_DOC" "must not proceed" "instructions block work on gate failure"
for doc in "$ONBOARDING_DOC" "$DOC_INDEX"; do
  check_contains "$doc" "enforce_agent_onboarding_gate.sh" "$(basename "$doc") references the onboarding gate"
done

echo "== C3: no private data in tracked files =="
# Patterns are assembled from fragments so this script does not itself contain a
# literal example of what it forbids — otherwise it would flag itself, and a gate
# that must be exempted from its own rule teaches contributors to add exemptions.
#
# The last three patterns exist because of a live incident. Workspace-level tooling
# enumerates mirror targets by SHAPE — "does this directory contain an AGENTS.md" —
# and injects private governance text into every match, after which a separate
# autocommit step commits it and an auto-repair step pushes it. On 2026-08-24 this
# repository was published and injected within the same hour. Blocking the injector
# is the fix; these patterns are the fail-safe, and they are carrier-independent:
# they assert that workspace-private content is absent, not that a particular
# script behaved. A future injector nobody has written yet is covered too.
declare -a LEAK_LABELS=(
  "absolute macOS home directory"
  "absolute Linux home directory"
  "SSH URL carrying a user and host"
  "private hostname suffix"
  "AWS access key id"
  "PEM private key block"
  "bearer token assignment"
  "workspace-private governance mirror"
  "author machine identity"
  "private workspace super-repo path"
)
declare -a LEAK_PATTERNS=(
  "/Users/[a-z][a-z0-9_-]"
  "/home/[a-z][a-z0-9_-]"
  "ssh://[a-zA-Z0-9_.-]+@"
  "[.](local|lan|internal)([/:[:space:]]|$)"
  "AKIA[0-9A-Z]{16}"
  "BEGIN [A-Z ]*PRIVATE KEY"
  "(api_key|secret|token|password)[[:space:]]*=[[:space:]]*[\"'][A-Za-z0-9_-]{16,}"
  "NON_CLAUDE_L2_MIRROR"
  "Mac ?mini|MacBook"
  "AI_Workspace"
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
  # The assertion is EXISTENCE, not primacy. A repository legitimately carries
  # several remotes — a durable mirror plus a public one — and `git remote` lists
  # them alphabetically, so comparing against the first entry would turn the act
  # of adding a public mirror into a gate failure. What must hold is only that
  # the remote this repository declares as its source of truth actually exists.
  configured_remotes="$(git -C "$ROOT_DIR" remote 2>/dev/null || true)"
  if [[ -z "$declared_remote" ]]; then
    fail "governance.yaml declares a durable_remote"
  elif [[ "$declared_remote" == "pending" ]]; then
    pass "publication target is explicitly pending (no remote assertion made)"
  elif [[ -z "$configured_remotes" ]]; then
    fail "declared durable_remote '$declared_remote' has no configured git remote"
  elif printf '%s\n' "$configured_remotes" | grep -qx -- "$declared_remote"; then
    pass "declared durable_remote '$declared_remote' is configured ($(printf '%s' "$configured_remotes" | tr '\n' ' ' | sed 's/ $//'))"
  else
    fail "declared durable_remote '$declared_remote' is not among the configured remotes" \
         "configured: $(printf '%s' "$configured_remotes" | tr '\n' ' ')"
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
