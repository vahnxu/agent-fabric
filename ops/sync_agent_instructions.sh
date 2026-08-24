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
MIRRORS=("$ROOT_DIR/CLAUDE.md" "$ROOT_DIR/GEMINI.md")

[[ -f "$CANONICAL" ]] || { echo "FAIL_LOUD: canonical AGENTS.md missing" >&2; exit 1; }

changed=0
for mirror in "${MIRRORS[@]}"; do
  if [[ -L "$mirror" ]]; then
    echo "INFO: $(basename "$mirror") is a symlink; leaving it as-is"
    continue
  fi
  if [[ -f "$mirror" ]] && cmp -s "$CANONICAL" "$mirror"; then
    echo "PASS: $(basename "$mirror") already matches AGENTS.md"
    continue
  fi
  cat "$CANONICAL" > "$mirror"
  echo "SYNCED: $(basename "$mirror") <- AGENTS.md"
  changed=$((changed + 1))
done

echo "RESULT: $changed file(s) updated"
