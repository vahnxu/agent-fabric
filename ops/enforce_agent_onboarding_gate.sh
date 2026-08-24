#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONSISTENCY_SCRIPT="$ROOT_DIR/ops/check_release_governance_consistency.sh"

AUTHORITATIVE_DOCS=(
  "$ROOT_DIR/AGENTS.md"
  "$ROOT_DIR/CLAUDE.md"
)

REFERENCE_DOCS=(
  "$ROOT_DIR/docs/workspace/NEW_AGENT_ONBOARDING_PROMPT.md"
  "$ROOT_DIR/docs/workspace/README.md"
)

REQUIRED_DOCS=( "${AUTHORITATIVE_DOCS[@]}" "${REFERENCE_DOCS[@]}" )

MANDATORY_PATTERNS=(
  "新 Agent 在执行任何生产相关任务前，必须"
  "./ops/check_release_governance_consistency.sh"
  "未通过不得执行生产变更任务"
)

fail() {
  echo "[FAIL] $1" >&2
  if [[ -n "${2:-}" ]]; then
    echo "FIX: $2" >&2
  fi
  exit 1
}

contains_fixed() {
  local pattern="$1"
  local file="$2"
  if command -v rg >/dev/null 2>&1; then
    rg -q --fixed-strings -- "$pattern" "$file"
  else
    grep -Fq -- "$pattern" "$file"
  fi
}

for file in "${REQUIRED_DOCS[@]}"; do
  [[ -f "$file" ]] || fail "required onboarding doc missing: $file" "创建缺失的文件: $file"
done

for doc in "${AUTHORITATIVE_DOCS[@]}"; do
  for pattern in "${MANDATORY_PATTERNS[@]}"; do
    if ! contains_fixed "$pattern" "$doc"; then
      fail "missing mandatory onboarding clause in $(basename "$doc"): $pattern" "在 $doc 中添加以下内容: $pattern"
    fi
  done
done

for doc in "${REFERENCE_DOCS[@]}"; do
  if ! contains_fixed "enforce_agent_onboarding_gate.sh" "$doc"; then
    fail "$(basename "$doc") does not reference enforce_agent_onboarding_gate.sh" "在 $doc 中添加引用"
  fi
done

[[ -x "$CONSISTENCY_SCRIPT" ]] || fail "consistency script missing or not executable: $CONSISTENCY_SCRIPT" "chmod +x $CONSISTENCY_SCRIPT"

if ! "$CONSISTENCY_SCRIPT"; then
  fail "governance consistency check failed" "运行 ./ops/check_release_governance_consistency.sh 查看具体失败项"
fi

LOCK_FILE="$(git -C "$ROOT_DIR" rev-parse --git-dir 2>/dev/null)/AGENT_ONBOARDING_PASSED"
if ! python3 - "$LOCK_FILE" <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import sys

try:
    Path(sys.argv[1]).write_text(
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ passed\n"),
        encoding="utf-8",
    )
except OSError:
    sys.exit(1)
PY
then
  echo "[WARN] could not persist onboarding marker: $LOCK_FILE" >&2
  echo "FIX: verify the git dir is writable; Codex sandbox may block .git writes, but gate checks still passed." >&2
fi

echo "[PASS] AGENT_ONBOARDING_GATE_PASSED"
