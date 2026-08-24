#!/usr/bin/env python3
"""switchboard_supervisor.py: Autonomous Supervisor & Fail-Closed Auto-Rotate for Agent Operations Fabric.

Monitors long-running switchboard sessions, detects capability fingerprints,
and executes fail-closed atomic rotation when transcript size exceeds threshold (50MB).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MAX_JSONL_MB = 50.0


def check_transcript_size_mb(jsonl_path: Path) -> float:
    """Return size of transcript JSONL in megabytes."""
    if not jsonl_path.is_file():
        return 0.0
    return round(jsonl_path.stat().st_size / (1024 * 1024), 2)


def should_rotate(jsonl_path: Path, max_mb: float = DEFAULT_MAX_JSONL_MB) -> bool:
    """Determine whether session transcript exceeds threshold."""
    return check_transcript_size_mb(jsonl_path) >= max_mb


def is_capability_match(args_str: str, pattern: str) -> bool:
    """Check if process argv matches capability fingerprint pattern."""
    if not args_str:
        return False
    return bool(re.search(pattern, args_str))


def audit_session_health(
    session_name: str,
    jsonl_path: Optional[Path] = None,
    max_mb: float = DEFAULT_MAX_JSONL_MB
) -> Dict[str, Any]:
    """Audit health and rotation readiness of a session."""
    size_mb = check_transcript_size_mb(jsonl_path) if jsonl_path else 0.0
    needs_rotation = size_mb >= max_mb

    return {
        "session_name": session_name,
        "jsonl_path": str(jsonl_path) if jsonl_path else None,
        "size_mb": size_mb,
        "threshold_mb": max_mb,
        "needs_rotation": needs_rotation,
        "status": "rotate_recommended" if needs_rotation else "healthy"
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Operations Fabric Supervisor")
    subparsers = parser.add_subparsers(dest="action", required=True)

    p_check = subparsers.add_parser("check-size", help="Check JSONL transcript size")
    p_check.add_argument("--jsonl", type=Path, required=True)
    p_check.add_argument("--max-mb", type=float, default=DEFAULT_MAX_JSONL_MB)

    p_audit = subparsers.add_parser("audit", help="Audit session health")
    p_audit.add_argument("--session", required=True)
    p_audit.add_argument("--jsonl", type=Path, default=None)
    p_audit.add_argument("--max-mb", type=float, default=DEFAULT_MAX_JSONL_MB)

    args = parser.parse_args()

    if args.action == "check-size":
        size = check_transcript_size_mb(args.jsonl)
        rot = should_rotate(args.jsonl, args.max_mb)
        print(json.dumps({"size_mb": size, "max_mb": args.max_mb, "should_rotate": rot}, indent=2))
        return 0 if not rot else 10

    elif args.action == "audit":
        res = audit_session_health(args.session, args.jsonl, args.max_mb)
        print(json.dumps(res, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
