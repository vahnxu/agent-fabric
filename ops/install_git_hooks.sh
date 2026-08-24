#!/usr/bin/env bash
# install_git_hooks.sh — arm the repository's gates as git hooks.
#
# WHY THIS EXISTS
#   A gate that only runs when a person types its name is not armed. The incident
#   that motivates this one is concrete: workspace tooling injected private
#   governance text into this repository's AGENTS.md, and a separate automated
#   step commits such regenerations without a human present. A leak scan that
#   waits to be invoked would never have seen it.
#
# WHAT GETS ARMED
#   pre-commit -> ops/check_release_governance_consistency.sh
#     Fast (about a second) and it is the check that matters at commit time: it
#     refuses a commit carrying private paths, machine identities, credential
#     material or an injected workspace mirror.
#
# WHAT DELIBERATELY DOES NOT GET ARMED HERE
#   The test suite and the negative-coverage gate run at push time and in CI.
#   Putting a ten-second suite on every commit is how hooks get disabled, and a
#   disabled hook protects nothing.
#
# KNOWN NON-COVERAGE
#   N1. `--no-verify` skips it. This hook raises the cost of an accident, not of
#       a decision; CI is the backstop that a local flag cannot reach.
#   N2. It inspects tracked files at commit time, so content already in published
#       history is out of reach — removing that needs a history rewrite, which is
#       a deliberate, owner-authorised act, not something a hook can do.
#   N3. A commit made in a different clone without hooks installed is unprotected.
#       Hooks are per-checkout; CI is what makes the check universal.
#
# Usage: ./ops/install_git_hooks.sh [--apply] [--check]
#   --check (default) reports what is and is not armed, exit 1 if anything is missing
#   --apply           writes the hooks
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GIT_DIR="$(git -C "$ROOT_DIR" rev-parse --git-dir 2>/dev/null || true)"
[[ -n "$GIT_DIR" ]] || { echo "FAIL_LOUD: not a git checkout: $ROOT_DIR" >&2; exit 1; }
[[ "$GIT_DIR" = /* ]] || GIT_DIR="$ROOT_DIR/$GIT_DIR"
HOOK_DIR="$GIT_DIR/hooks"
PRE_COMMIT="$HOOK_DIR/pre-commit"
MARKER="agent-fabric-governance-hook-v1"

MODE="check"
for arg in "$@"; do
  case "$arg" in
    --apply) MODE="apply" ;;
    --check) MODE="check" ;;
    *) echo "usage: $0 [--apply|--check]" >&2; exit 2 ;;
  esac
done

read -r -d '' HOOK_BODY <<'HOOK' || true
#!/usr/bin/env bash
# agent-fabric-governance-hook-v1 — installed by ops/install_git_hooks.sh
# Refuses a commit that carries private data or an injected workspace mirror.
# Regenerate with: ./ops/install_git_hooks.sh --apply
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
GATE="$REPO/ops/check_release_governance_consistency.sh"
[[ -x "$GATE" ]] || exit 0
if ! "$GATE" > /tmp/agent_fabric_precommit.$$ 2>&1; then
    echo "FAIL_LOUD [pre-commit]: governance consistency check refused this commit." >&2
    grep -E "^(FAIL|    )" /tmp/agent_fabric_precommit.$$ >&2 || cat /tmp/agent_fabric_precommit.$$ >&2
    echo "" >&2
    echo "If a tool wrote private content into a tracked file, revert that file —" >&2
    echo "do NOT run the instruction-sync script, which would copy it into the mirrors." >&2
    exit 1
fi
exit 0
HOOK

if [[ "$MODE" == "apply" ]]; then
  mkdir -p "$HOOK_DIR"
  if [[ -f "$PRE_COMMIT" ]] && ! grep -q "$MARKER" "$PRE_COMMIT"; then
    backup="$PRE_COMMIT.pre-agent-fabric.$(date -u +%Y%m%dT%H%M%SZ)"
    cp -n "$PRE_COMMIT" "$backup"
    echo "NOTE: existing pre-commit hook preserved at $backup"
  fi
  printf '%s\n' "$HOOK_BODY" > "$PRE_COMMIT"
  chmod +x "$PRE_COMMIT"
  echo "ARMED: pre-commit -> ops/check_release_governance_consistency.sh"
  exit 0
fi

if [[ -f "$PRE_COMMIT" ]] && grep -q "$MARKER" "$PRE_COMMIT" && [[ -x "$PRE_COMMIT" ]]; then
  echo "PASS: pre-commit hook is armed ($MARKER)"
  exit 0
fi

echo "FAIL: pre-commit hook is not armed" >&2
echo "FIX: ./ops/install_git_hooks.sh --apply" >&2
exit 1
