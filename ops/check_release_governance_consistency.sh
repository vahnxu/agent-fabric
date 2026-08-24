#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

AGENTS_DOC="$ROOT_DIR/AGENTS.md"
CLAUDE_DOC="$ROOT_DIR/CLAUDE.md"
GOVERNANCE_YAML="$ROOT_DIR/governance.yaml"
DOC_INDEX="$ROOT_DIR/docs/workspace/README.md"
NEW_AGENT_ONBOARDING_DOC="$ROOT_DIR/docs/workspace/NEW_AGENT_ONBOARDING_PROMPT.md"
ONBOARDING_GATE_SCRIPT="$ROOT_DIR/ops/enforce_agent_onboarding_gate.sh"
GITIGNORE_FILE="$ROOT_DIR/.gitignore"

# ─── Governance source selection ───
YAML_MANDATORY_AFTER="2026-06-30"
GOVERNANCE_SOURCE="heuristic"
PROJECT_TYPE="minimal"

if [[ -f "$GOVERNANCE_YAML" ]]; then
  GOVERNANCE_SOURCE="yaml"
  if command -v ruby >/dev/null 2>&1; then
    PROJECT_TYPE="$(ruby -ryaml -e 'puts YAML.safe_load(File.read(ARGV[0]))["type"]' "$GOVERNANCE_YAML")"
  fi
else
  today="$(date +%Y-%m-%d)"
  if [[ "$today" > "$YAML_MANDATORY_AFTER" ]]; then
    echo "FAIL: governance.yaml is required after $YAML_MANDATORY_AFTER" >&2
    exit 1
  fi
  echo "WARN: governance.yaml missing; fallback heuristic before $YAML_MANDATORY_AFTER"
fi

echo "INFO: PROJECT_TYPE=$PROJECT_TYPE source=$GOVERNANCE_SOURCE"

fail_count=0
SEARCH_TOOL="grep"
if command -v rg >/dev/null 2>&1; then
  SEARCH_TOOL="rg"
fi

contains_match() {
  local pattern="$1"
  local file="$2"
  if [[ "$SEARCH_TOOL" == "rg" ]]; then
    rg -q --fixed-strings -- "$pattern" "$file"
  else
    grep -Fq -- "$pattern" "$file"
  fi
}

check_contains() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if contains_match "$pattern" "$file"; then
    echo "PASS: $label"
  else
    echo "FAIL: $label"
    echo "  missing pattern: $pattern"
    echo "  file: $file"
    fail_count=$((fail_count + 1))
  fi
}

check_file_exists() {
  local file="$1"
  local label="$2"
  if [[ -f "$file" ]]; then
    echo "PASS: $label"
  else
    echo "FAIL: $label"
    echo "  missing file: $file"
    fail_count=$((fail_count + 1))
  fi
}

echo "== Check 1: Agent docs governance rules =="
for doc in "$AGENTS_DOC" "$CLAUDE_DOC"; do
  check_contains "$doc" "唯一真相源：本地 git \`main\` + 当前配置的 durable remote" "$(basename "$doc") has local-only durable publish contract"
  check_contains "$doc" "不要把 Mac mini bare \`origin\` 误判为“禁止 push 的远程”" "$(basename "$doc") distinguishes Mac mini bare from forbidden GitHub remote"
done

if [[ -f "$GOVERNANCE_YAML" ]] && command -v ruby >/dev/null 2>&1; then
  GITHUB_MODE="$(ruby -ryaml -e 'puts YAML.safe_load(File.read(ARGV[0]))["github"] || ""' "$GOVERNANCE_YAML")"
  TRUTH_SOURCE="$(ruby -ryaml -e 'puts((YAML.safe_load(File.read(ARGV[0]))["sync"] || {})["truth_source"] || "")' "$GOVERNANCE_YAML")"
  ORIGIN_URL="$(git -C "$ROOT_DIR" remote get-url origin 2>/dev/null || true)"
  if [[ "$GITHUB_MODE" == "local-only" && "$TRUTH_SOURCE" == "local" && "$ORIGIN_URL" == *"/Users/example/repos/"*.git ]]; then
    echo "PASS: local-only origin is configured as Mac mini bare durable remote"
  elif [[ "$GITHUB_MODE" == "local-only" && "$TRUTH_SOURCE" == "local" && -n "$ORIGIN_URL" ]]; then
    echo "FAIL: local-only origin is not recognized as Mac mini bare durable remote"
    echo "  origin: $ORIGIN_URL"
    echo "  expected: ssh://<user>@<macmini-host>/Users/example/repos/<repo>.git"
    fail_count=$((fail_count + 1))
  fi
fi

echo "== Check 1b: Onboarding hard gate clauses =="
check_contains "$AGENTS_DOC" "新 Agent 在执行任何生产相关任务前，必须" "AGENTS.md has onboarding prerequisite clause"
check_contains "$AGENTS_DOC" "./ops/check_release_governance_consistency.sh" "AGENTS.md requires consistency command"
check_contains "$AGENTS_DOC" "未通过不得执行生产变更任务" "AGENTS.md blocks production work on gate failure"
check_contains "$CLAUDE_DOC" "新 Agent 在执行任何生产相关任务前，必须" "CLAUDE.md has onboarding prerequisite clause"
check_contains "$CLAUDE_DOC" "./ops/check_release_governance_consistency.sh" "CLAUDE.md requires consistency command"
check_contains "$CLAUDE_DOC" "未通过不得执行生产变更任务" "CLAUDE.md blocks production work on gate failure"

for doc in "$NEW_AGENT_ONBOARDING_DOC" "$DOC_INDEX"; do
  check_contains "$doc" "enforce_agent_onboarding_gate.sh" "$(basename "$doc") references onboarding gate"
done

check_file_exists "$ONBOARDING_GATE_SCRIPT" "onboarding gate script exists"
check_contains "$ONBOARDING_GATE_SCRIPT" "AGENT_ONBOARDING_GATE_PASSED" "onboarding gate prints pass marker"
check_contains "$ONBOARDING_GATE_SCRIPT" "check_release_governance_consistency.sh" "onboarding gate runs consistency check"

echo "== Check 2: .gitignore basics =="
check_contains "$GITIGNORE_FILE" ".DS_Store" ".gitignore excludes .DS_Store"
check_contains "$GITIGNORE_FILE" ".env" ".gitignore excludes .env"
check_contains "$GITIGNORE_FILE" "__pycache__" ".gitignore excludes __pycache__"

echo "== Check 3: Governance files exist =="
check_file_exists "$GOVERNANCE_YAML" "governance.yaml exists"
check_file_exists "$DOC_INDEX" "docs/workspace/README.md exists"
check_file_exists "$NEW_AGENT_ONBOARDING_DOC" "docs/workspace/NEW_AGENT_ONBOARDING_PROMPT.md exists"

if [[ "$fail_count" -gt 0 ]]; then
  echo "== RESULT: FAIL ($fail_count checks failed) =="
  exit 1
fi

echo "== RESULT: PASS (all checks passed) =="
