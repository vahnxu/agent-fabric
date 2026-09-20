#!/usr/bin/env bash
# sync_agent_instructions.sh — keep the per-runtime instruction files identical.
#
# AGENTS.md is canonical. CLAUDE.md and GEMINI.md are copies of it, because
# different agent runtimes look for different filenames and none of them should
# get a different set of rules. Drift between them is the failure this prevents:
# a rule fixed in one file and stale in another is worse than no rule, since each
# runtime believes it is reading the authority.
#
# Copies rather than symlinks: this repository is published, and symlinks do not
# survive every checkout environment.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$ROOT_DIR/AGENTS.md"
# 2026-09-20 (owner decision): CLAUDE.md is a one-line loader, not a copy.
# Copying AGENTS.md over it would re-create a second hand-written instruction
# face — exactly what the migration removed. GEMINI.md stays a symlink.
CANONICAL_LOADER='@AGENTS.md'

[[ -f "$CANONICAL" ]] || { echo "FAIL_LOUD: canonical AGENTS.md missing" >&2; exit 1; }

changed=0

if [[ -L "$ROOT_DIR/GEMINI.md" ]]; then
  echo "PASS: GEMINI.md is a symlink to AGENTS.md; leaving it as-is"
else
  ln -sf AGENTS.md "$ROOT_DIR/GEMINI.md"
  echo "SYNCED: GEMINI.md -> AGENTS.md (symlink)"
  changed=$((changed + 1))
fi

if [[ -f "$ROOT_DIR/CLAUDE.md" ]] && [[ "$(cat "$ROOT_DIR/CLAUDE.md")" == "$CANONICAL_LOADER" ]]; then
  echo "PASS: CLAUDE.md is already the canonical loader"
else
  printf '%s\n' "$CANONICAL_LOADER" > "$ROOT_DIR/CLAUDE.md"
  echo "SYNCED: CLAUDE.md <- canonical loader ($CANONICAL_LOADER)"
  changed=$((changed + 1))
fi

echo "RESULT: $changed file(s) updated"
