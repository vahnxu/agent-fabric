#!/bin/bash
# pre_tool_use_safety.sh: Intercept dangerous commands at the OS/Tool boundary.
# Part of Agent Operations Fabric Governance Mesh.
set -euo pipefail

COMMAND="${1:-}"

if [[ -z "$COMMAND" ]]; then
    exit 0
fi

# 1. 物理拦截直接 rm 操作，强制改走回收站或改名备份
if [[ "$COMMAND" =~ (^|[[:space:]]|;)rm([[:space:]]|$) ]]; then
    echo "FAIL_LOUD [PreToolUse Safety Intercept]: Direct 'rm' is strictly prohibited by Agent Operations Fabric." >&2
    echo "Reason: Deletion must be reversible. Please move to Trash or rename with timestamp instead." >&2
    exit 2
fi

# 2. 拦截可能静默覆盖重要文件的裸写入 (cp -f / mv -f)
if [[ "$COMMAND" =~ (^|[[:space:]]|;)cp[[:space:]]+-f || "$COMMAND" =~ (^|[[:space:]]|;)mv[[:space:]]+-f ]]; then
    echo "FAIL_LOUD [PreToolUse Safety Intercept]: Force-overwrite (cp -f / mv -f) is prohibited." >&2
    echo "Reason: Overwrite is irreversible. Please backup target file before writing." >&2
    exit 2
fi

exit 0
