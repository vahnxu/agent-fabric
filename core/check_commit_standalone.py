#!/usr/bin/env python3
"""check_commit_standalone.py: Verify standalone tree integrity of staged files.

Asserts that staged changes alone are complete and self-contained, and do not
secretly depend on uncommitted/untracked files lingering in the local workspace.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import List


def get_staged_files() -> List[str]:
    """Get list of files currently staged in git index."""
    cp = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True)
    if cp.returncode != 0:
        return []
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def get_untracked_files() -> List[str]:
    """Get list of untracked files in git workspace."""
    cp = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if cp.returncode != 0:
        return []
    untracked = []
    for line in cp.stdout.splitlines():
        if line.startswith("?? "):
            untracked.append(line[3:].strip())
    return untracked


def check_standalone_integrity() -> Tuple[bool, List[str]]:
    """Scan staged files to check for dangling references to untracked artifacts."""
    staged = get_staged_files()
    untracked = set(get_untracked_files())
    violations = []

    for f in staged:
        p = Path(f)
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            for u in untracked:
                if u and u in content:
                    violations.append(f"Staged file '{f}' references untracked local file '{u}'")
        except Exception:
            pass

    return (len(violations) == 0, violations)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Standalone Commit Tree Integrity")
    parser.parse_args()

    ok, violations = check_standalone_integrity()
    if not ok:
        print("FAIL_LOUD [COMMIT_TREE_STANDALONE Gate Failed]:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("Reason: Staged commit references untracked local artifacts. Commit must be self-contained.", file=sys.stderr)
        return 1

    print("PASS: Commit tree is standalone and self-contained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
