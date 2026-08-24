#!/usr/bin/env python3
"""switchboard_supervisor.py — keeps a long-lived dispatcher session alive forever.

THE PROBLEM
    A dispatcher session that stays up for weeks accumulates a transcript. Past a
    few tens of megabytes the process memory-thrashes and eventually dies, and the
    messages that arrived while it was dead are simply lost. Restarting it by hand
    means noticing first, which nobody does at 3am.

THE MECHANISM: fail-closed atomic rotation
    Rotation is a five-step state machine, and the ordering is the whole point —
    the new session must prove it has taken over the channel BEFORE the old one
    stops serving. At no instant are there zero servers.

        measure ──> spawn(shadow) ──> handshake ──> promote ──> retire(old)
                         │                 │
                         │                 └── probe failed N times
                         └── could not start ──┐
                                               ▼
                                     destroy(shadow); old keeps serving
                                     result = rolled_back  (NOT an outage)

    · measure   — transcript size crosses the threshold
    · spawn     — a cold, empty shadow session starts under a temporary name
    · handshake — the shadow must answer on the real message channel; this is the
                  only evidence accepted that it is genuinely able to serve
    · promote   — the shadow takes the official name
    · retire    — only now does the old session stop, and its transcript is kept
    · rollback  — any failure destroys the shadow and leaves the old session
                  untouched. Refusing to rotate is always safe; rotating into an
                  unproven session is not.

FAILURE MODES AND WHAT HAPPENS
    · shadow will not start        -> rolled_back, old session still serving
    · shadow never answers a probe -> shadow destroyed, rolled_back
    · promote raises mid-flight    -> shadow destroyed, rolled_back, old serving
    · retire raises after promote  -> reported as `retire_failed`, NOT as failure:
                                      the new session is already serving, so the
                                      only residue is a stale process to reap.

ROLLBACK POINT
    Nothing here mutates durable state. The old session's transcript is never
    truncated or removed — retirement archives it. If a rotation goes wrong the
    recovery action is to keep using the old session, which is exactly what the
    rolled_back path leaves you with.

RUNTIME DECOUPLING
    This module never names a CLI, a vendor or a model. Everything vendor-specific
    is a callable supplied by the caller through SessionOps: how to start a
    session, how to probe it, how to rename it, how to stop it. Sessions are
    matched by CAPABILITY FINGERPRINT — a regex over the process argv describing
    what the process can do (which channel it serves) — never by product name.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_MAX_JSONL_MB = 50.0
DEFAULT_HANDSHAKE_ATTEMPTS = 3
DEFAULT_HANDSHAKE_INTERVAL_S = 2.0


# ── measurement ───────────────────────────────────────────────────────────────

def check_transcript_size_mb(jsonl_path: Optional[Path]) -> float:
    """Size of a session transcript in MB. A missing file measures 0.0."""
    if jsonl_path is None or not Path(jsonl_path).is_file():
        return 0.0
    return round(Path(jsonl_path).stat().st_size / (1024 * 1024), 2)


def should_rotate(jsonl_path: Optional[Path], max_mb: float = DEFAULT_MAX_JSONL_MB) -> bool:
    return check_transcript_size_mb(jsonl_path) >= max_mb


def is_capability_match(args_str: str, pattern: str) -> bool:
    """Does this process argv show the capability we need? Never a vendor name."""
    if not args_str:
        return False
    return bool(re.search(pattern, args_str))


def find_sessions_by_capability(pattern: str) -> List[Dict[str, str]]:
    """Enumerate live processes whose argv advertises the given capability."""
    try:
        cp = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if cp.returncode != 0:
        return []
    found = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        pid, _, args = line.partition(" ")
        if is_capability_match(args, pattern):
            found.append({"pid": pid, "args": args.strip()})
    return found


def audit_session_health(
    session_name: str,
    jsonl_path: Optional[Path] = None,
    max_mb: float = DEFAULT_MAX_JSONL_MB,
) -> Dict[str, Any]:
    size_mb = check_transcript_size_mb(jsonl_path)
    needs_rotation = size_mb >= max_mb
    return {
        "session_name": session_name,
        "jsonl_path": str(jsonl_path) if jsonl_path else None,
        "size_mb": size_mb,
        "threshold_mb": max_mb,
        "needs_rotation": needs_rotation,
        "status": "rotate_recommended" if needs_rotation else "healthy",
    }


# ── rotation ──────────────────────────────────────────────────────────────────

class RotationError(Exception):
    """Raised only when the invariant itself was broken, never for a clean rollback."""


@dataclass
class SessionOps:
    """The runtime-specific half of rotation, injected by the caller.

    spawn(shadow_name)    -> an opaque handle for the new session
    handshake(handle)     -> True once the new session answers on the real channel
    promote(handle, name) -> give the new session the official name
    retire(old_name)      -> stop and archive the outgoing session
    destroy(handle)       -> tear the shadow down (rollback path)
    """

    spawn: Callable[[str], Any]
    handshake: Callable[[Any], bool]
    promote: Callable[[Any, str], None]
    retire: Callable[[str], None]
    destroy: Callable[[Any], None]


@dataclass
class RotationResult:
    outcome: str  # rotated | rolled_back | not_needed | retire_failed
    reason: str
    steps: List[str] = field(default_factory=list)
    handshake_attempts: int = 0
    size_mb: float = 0.0

    @property
    def service_preserved(self) -> bool:
        """True when a session is serving at the end. Must hold in every outcome."""
        return self.outcome in ("rotated", "rolled_back", "not_needed", "retire_failed")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "steps": self.steps,
            "handshake_attempts": self.handshake_attempts,
            "size_mb": self.size_mb,
            "service_preserved": self.service_preserved,
        }


def rotate_session(
    session_name: str,
    ops: SessionOps,
    jsonl_path: Optional[Path] = None,
    max_mb: float = DEFAULT_MAX_JSONL_MB,
    attempts: int = DEFAULT_HANDSHAKE_ATTEMPTS,
    interval_s: float = DEFAULT_HANDSHAKE_INTERVAL_S,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> RotationResult:
    """Run one fail-closed rotation cycle. Never leaves the channel unserved."""
    size_mb = check_transcript_size_mb(jsonl_path)
    if not force and size_mb < max_mb:
        return RotationResult(
            outcome="not_needed",
            reason=f"transcript at {size_mb}MB is below the {max_mb}MB threshold",
            steps=["measure"],
            size_mb=size_mb,
        )

    steps = ["measure"]
    shadow_name = f"{session_name}--shadow"

    # 1. spawn the shadow
    try:
        handle = ops.spawn(shadow_name)
    except Exception as exc:
        return RotationResult(
            outcome="rolled_back",
            reason=f"shadow session failed to start: {exc}. Old session keeps serving.",
            steps=steps + ["spawn_failed"],
            size_mb=size_mb,
        )
    if handle is None:
        return RotationResult(
            outcome="rolled_back",
            reason="shadow session returned no handle. Old session keeps serving.",
            steps=steps + ["spawn_failed"],
            size_mb=size_mb,
        )
    steps.append("spawn")

    # 2. the shadow must prove it can serve before anything else moves
    took_over = False
    used_attempts = 0
    for used_attempts in range(1, attempts + 1):
        try:
            took_over = bool(ops.handshake(handle))
        except Exception:
            took_over = False
        if took_over:
            break
        if used_attempts < attempts:
            sleep(interval_s)

    if not took_over:
        _destroy_quietly(ops, handle, steps)
        return RotationResult(
            outcome="rolled_back",
            reason=(
                f"shadow session did not answer on the channel after {used_attempts} probe(s); "
                "it was destroyed and the old session keeps serving"
            ),
            steps=steps,
            handshake_attempts=used_attempts,
            size_mb=size_mb,
        )
    steps.append("handshake")

    # 3. promote — from here the shadow is the one serving
    try:
        ops.promote(handle, session_name)
    except Exception as exc:
        _destroy_quietly(ops, handle, steps)
        return RotationResult(
            outcome="rolled_back",
            reason=f"promotion failed: {exc}. Shadow destroyed; old session keeps serving.",
            steps=steps,
            handshake_attempts=used_attempts,
            size_mb=size_mb,
        )
    steps.append("promote")

    # 4. retire the outgoing session. A failure here is untidy, not an outage —
    #    the new session is already serving, so we must not roll back into it.
    try:
        ops.retire(session_name)
    except Exception as exc:
        return RotationResult(
            outcome="retire_failed",
            reason=(
                f"new session is serving, but the old one could not be retired: {exc}. "
                "Reap the stale process; do not roll back."
            ),
            steps=steps + ["retire_failed"],
            handshake_attempts=used_attempts,
            size_mb=size_mb,
        )
    steps.append("retire")

    return RotationResult(
        outcome="rotated",
        reason=f"rotated at {size_mb}MB after {used_attempts} probe(s)",
        steps=steps,
        handshake_attempts=used_attempts,
        size_mb=size_mb,
    )


def _destroy_quietly(ops: SessionOps, handle: Any, steps: List[str]) -> None:
    """Tear down the shadow. A failure to destroy must not mask the rollback."""
    try:
        ops.destroy(handle)
        steps.append("rollback_destroy")
    except Exception:
        steps.append("rollback_destroy_failed")


# ── dead-window reporting ─────────────────────────────────────────────────────

def describe_dead_window(last_seen_epoch: float, now_epoch: float) -> Dict[str, Any]:
    """State the exact interval during which nothing was serving.

    A reconnect that does not say what it missed is worse than a visible outage:
    it looks like nothing happened.
    """
    gap = max(0.0, now_epoch - last_seen_epoch)
    return {
        "dead_from_epoch": last_seen_epoch,
        "dead_until_epoch": now_epoch,
        "dead_seconds": round(gap, 1),
        "messages_possibly_missed": gap > 0,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Agent Operations Fabric switchboard supervisor")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("check-size", help="measure a transcript against the threshold")
    p.add_argument("--jsonl", type=Path, required=True)
    p.add_argument("--max-mb", type=float, default=DEFAULT_MAX_JSONL_MB)

    p = sub.add_parser("audit", help="report health and rotation readiness")
    p.add_argument("--session", required=True)
    p.add_argument("--jsonl", type=Path, default=None)
    p.add_argument("--max-mb", type=float, default=DEFAULT_MAX_JSONL_MB)

    p = sub.add_parser("find", help="list live sessions advertising a capability")
    p.add_argument("--capability", required=True, help="regex matched against process argv")

    args = parser.parse_args(argv)

    if args.action == "check-size":
        size = check_transcript_size_mb(args.jsonl)
        rot = should_rotate(args.jsonl, args.max_mb)
        print(json.dumps({"size_mb": size, "max_mb": args.max_mb, "should_rotate": rot}, indent=2))
        return 10 if rot else 0

    if args.action == "audit":
        print(json.dumps(audit_session_health(args.session, args.jsonl, args.max_mb), indent=2))
        return 0

    if args.action == "find":
        found = find_sessions_by_capability(args.capability)
        print(json.dumps(found, indent=2))
        return 0 if found else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
