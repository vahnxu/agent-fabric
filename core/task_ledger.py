#!/usr/bin/env python3
"""task_ledger.py — the transactional state machine that owns task completion.

WHY THIS EXISTS
    A dispatched worker reporting "done" in prose is not evidence. The ledger is
    the only component allowed to declare a task closed, and it will only do so
    against evidence it can check itself. Two rules carry the whole design:

      R1. A worker cannot close its own task. `receive_result` records what the
          worker claims; it moves the task to `result_received` and no further.
          Closing is a separate call made by the dispatcher.
      R2. Closing requires evidence that satisfies a declared contract — by
          default a commit ref plus a passing test exit code. Evidence that does
          not satisfy the contract is refused, loudly, and the task stays open.

STATE MACHINE
    created ──> dispatched ──> result_received ──> verified ──> closed
       │             │                │                │
       └─────────────┴────────────────┴────────────────┴──> cancelled

    Any transition not in TRANSITIONS is refused. The status column is not a free
    text field; an out-of-order write is a bug in the caller, and the ledger says
    so rather than silently recording it.

FAILURE MODES AND WHAT HAPPENS
    · task_id does not exist            -> TaskNotFoundError, exit 4, nothing written
    · transition not permitted          -> IllegalTransitionError, exit 5, nothing written
    · evidence fails the contract       -> EvidenceRejectedError, exit 6, task stays open
    Every one of these is a non-zero exit with the reason on stderr. There is no
    path through this module that reports success without a row having changed.

ROLLBACK
    State lives in a single SQLite file (FABRIC_STATE_DIR, or --db). Copy it
    before a risky batch; restoring the file restores the ledger exactly. Task
    rows are never deleted — `cancel` is the terminal state for abandoned work,
    so the audit trail survives.

Runtime-agnostic: no CLI vendor name or model name appears in this module. A
worker is identified by an opaque session string supplied by the caller.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VALID_STATUSES: Tuple[str, ...] = (
    "created",
    "dispatched",
    "result_received",
    "verified",
    "closed",
    "cancelled",
)

TERMINAL_STATUSES: Tuple[str, ...] = ("closed", "cancelled")

#: The only permitted moves. Everything else is refused.
TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "created": ("dispatched", "cancelled"),
    "dispatched": ("result_received", "cancelled"),
    "result_received": ("verified", "dispatched", "cancelled"),
    "verified": ("closed", "cancelled"),
    "closed": (),
    "cancelled": (),
}

#: Keys a completion claim must carry before the ledger will verify it.
DEFAULT_REQUIRED_EVIDENCE: Tuple[str, ...] = ("commit", "tests_exit_code")


class LedgerError(Exception):
    """Base class. Every subclass maps to a distinct process exit code."""

    exit_code = 1


class TaskNotFoundError(LedgerError):
    exit_code = 4


class IllegalTransitionError(LedgerError):
    exit_code = 5


class EvidenceRejectedError(LedgerError):
    exit_code = 6


# ── storage ───────────────────────────────────────────────────────────────────

def get_default_db_path() -> Path:
    state_dir = Path(
        os.environ.get("FABRIC_STATE_DIR", Path.home() / ".local" / "state" / "agent_fabric")
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "task_ledger.sqlite"


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_ledger (
                task_id        TEXT PRIMARY KEY,
                route          TEXT NOT NULL,
                status         TEXT NOT NULL,
                summary        TEXT,
                worker_session TEXT,
                result_claim   TEXT,
                evidence       TEXT,
                created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_transitions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT NOT NULL,
                from_state TEXT,
                to_state   TEXT NOT NULL,
                note       TEXT,
                at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def _fetch(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM task_ledger WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFoundError(
            f"task '{task_id}' does not exist in this ledger. "
            "Nothing was written. Create it before transitioning it."
        )
    return row


def _assert_transition(current: str, target: str, task_id: str) -> None:
    permitted = TRANSITIONS.get(current, ())
    if target not in permitted:
        allowed = ", ".join(permitted) if permitted else "(terminal state — no moves)"
        raise IllegalTransitionError(
            f"task '{task_id}' is in '{current}'; '{target}' is not reachable from there. "
            f"Permitted: {allowed}. Nothing was written."
        )


def _apply(
    conn: sqlite3.Connection,
    task_id: str,
    current: str,
    target: str,
    assignments: Dict[str, Any],
    note: str = "",
) -> None:
    _assert_transition(current, target, task_id)
    assignments = dict(assignments)
    assignments["status"] = target
    columns = ", ".join(f"{k}=?" for k in assignments)
    values = list(assignments.values()) + [task_id]
    cur = conn.execute(
        f"UPDATE task_ledger SET {columns}, updated_at=CURRENT_TIMESTAMP WHERE task_id=?",
        values,
    )
    if cur.rowcount != 1:
        # Defensive: _fetch already proved the row exists, so a zero here means a
        # concurrent writer removed it. Refuse rather than report a phantom move.
        raise TaskNotFoundError(
            f"task '{task_id}' vanished mid-transition ({cur.rowcount} rows affected)."
        )
    conn.execute(
        "INSERT INTO task_transitions (task_id, from_state, to_state, note) VALUES (?,?,?,?)",
        (task_id, current, target, note),
    )


# ── evidence contract ─────────────────────────────────────────────────────────

def default_evidence_validator(evidence: Dict[str, Any]) -> List[str]:
    """Return a list of reasons the evidence is unacceptable. Empty list = accept.

    Deliberately mechanical: it checks that the claim carries the fields a human
    would need to re-verify the work by hand, and that a declared test run
    actually passed. It does not re-run anything — that is the dispatcher's job,
    and a custom validator is the place to add it.
    """
    reasons: List[str] = []
    if not isinstance(evidence, dict):
        return [f"evidence must be a mapping, got {type(evidence).__name__}"]

    # An explicit null is absence, not a value. Treating "key is present" as
    # satisfaction is how a contract check turns into a formality.
    missing = object()
    for key in DEFAULT_REQUIRED_EVIDENCE:
        if evidence.get(key, missing) is missing or evidence.get(key) is None:
            reasons.append(f"missing required evidence field '{key}'")

    commit = evidence.get("commit")
    if commit is not None:
        if not isinstance(commit, str) or len(commit.strip()) < 7:
            reasons.append(
                "evidence field 'commit' must be a ref of at least 7 characters "
                f"(got {commit!r})"
            )

    exit_code = evidence.get("tests_exit_code")
    if exit_code is not None:
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            reasons.append(
                f"evidence field 'tests_exit_code' must be an integer (got {exit_code!r})"
            )
        elif exit_code != 0:
            reasons.append(
                f"declared test run failed (tests_exit_code={exit_code}); "
                "a red suite cannot close a task"
            )
    return reasons


EvidenceValidator = Callable[[Dict[str, Any]], List[str]]


# ── operations ────────────────────────────────────────────────────────────────

def create_task(db_path: Path, task_id: str, route: str, summary: str) -> Dict[str, Any]:
    init_db(db_path)
    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT status FROM task_ledger WHERE task_id=?", (task_id,)
        ).fetchone()
        if existing is not None:
            raise IllegalTransitionError(
                f"task '{task_id}' already exists in state '{existing['status']}'. "
                "Task ids are not reusable — the audit trail depends on it."
            )
        conn.execute(
            "INSERT INTO task_ledger (task_id, route, status, summary) VALUES (?,?,'created',?)",
            (task_id, route, summary),
        )
        conn.execute(
            "INSERT INTO task_transitions (task_id, from_state, to_state, note) VALUES (?,?,?,?)",
            (task_id, None, "created", summary),
        )
        conn.commit()
    return {"task_id": task_id, "route": route, "status": "created", "summary": summary}


def dispatch_task(
    db_path: Path, task_id: str, worker_session: str, route: Optional[str] = None
) -> Dict[str, Any]:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _fetch(conn, task_id)
        assignments: Dict[str, Any] = {"worker_session": worker_session}
        if route:
            assignments["route"] = route
        _apply(conn, task_id, row["status"], "dispatched", assignments, note=worker_session)
        conn.commit()
    return {"task_id": task_id, "status": "dispatched", "worker_session": worker_session}


def receive_result(db_path: Path, task_id: str, result_summary: str) -> Dict[str, Any]:
    """Record what the worker claims. This does NOT close the task (rule R1)."""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _fetch(conn, task_id)
        _apply(
            conn,
            task_id,
            row["status"],
            "result_received",
            {"result_claim": result_summary},
            note="worker claim recorded; not yet verified",
        )
        conn.commit()
    return {"task_id": task_id, "status": "result_received", "claim": result_summary}


def verify_task(
    db_path: Path,
    task_id: str,
    evidence: Dict[str, Any],
    validator: Optional[EvidenceValidator] = None,
) -> Dict[str, Any]:
    """Check the completion evidence. Refuses and leaves the task open on failure."""
    init_db(db_path)
    check = validator or default_evidence_validator
    reasons = check(evidence)
    if reasons:
        raise EvidenceRejectedError(
            f"evidence for task '{task_id}' rejected; the task remains open:\n  - "
            + "\n  - ".join(reasons)
        )
    with _connect(db_path) as conn:
        row = _fetch(conn, task_id)
        _apply(
            conn,
            task_id,
            row["status"],
            "verified",
            {"evidence": json.dumps(evidence, ensure_ascii=False)},
            note="evidence accepted",
        )
        conn.commit()
    return {"task_id": task_id, "status": "verified", "evidence": evidence}


def close_task(db_path: Path, task_id: str) -> Dict[str, Any]:
    """Terminal close. Only reachable from `verified` — see TRANSITIONS."""
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _fetch(conn, task_id)
        _apply(conn, task_id, row["status"], "closed", {}, note="closed by dispatcher")
        conn.commit()
    return {"task_id": task_id, "status": "closed"}


def verify_and_close(
    db_path: Path,
    task_id: str,
    evidence: Dict[str, Any],
    validator: Optional[EvidenceValidator] = None,
) -> Dict[str, Any]:
    """Convenience: verify then close. Raises before closing if evidence fails."""
    verify_task(db_path, task_id, evidence, validator)
    result = close_task(db_path, task_id)
    result["evidence"] = evidence
    return result


def cancel_task(db_path: Path, task_id: str, reason: str) -> Dict[str, Any]:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = _fetch(conn, task_id)
        _apply(conn, task_id, row["status"], "cancelled", {}, note=reason)
        conn.commit()
    return {"task_id": task_id, "status": "cancelled", "reason": reason}


def list_active_tasks(db_path: Path) -> List[Dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as conn:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        cur = conn.execute(
            f"""
            SELECT task_id, route, status, summary, worker_session, updated_at
            FROM task_ledger WHERE status NOT IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            TERMINAL_STATUSES,
        )
        return [dict(row) for row in cur.fetchall()]


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM task_ledger WHERE task_id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_history(db_path: Path, task_id: str) -> List[Dict[str, Any]]:
    """Every transition this task has been through, oldest first."""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "SELECT from_state, to_state, note, at FROM task_transitions "
            "WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        )
        return [dict(row) for row in cur.fetchall()]


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Operations Fabric task ledger")
    parser.add_argument("--db", type=Path, default=None, help="path to the sqlite ledger")
    sub = parser.add_subparsers(dest="action", required=True)

    p = sub.add_parser("create", help="register a new task")
    p.add_argument("--task-id", required=True)
    p.add_argument("--route", default="general")
    p.add_argument("--summary", default="")

    p = sub.add_parser("dispatch", help="hand the task to a worker session")
    p.add_argument("--task-id", required=True)
    p.add_argument("--worker-session", required=True)
    p.add_argument("--route", default=None)

    p = sub.add_parser("receive-result", help="record the worker's claim (does not close)")
    p.add_argument("--task-id", required=True)
    p.add_argument("--summary", required=True)

    p = sub.add_parser("verify", help="check completion evidence against the contract")
    p.add_argument("--task-id", required=True)
    p.add_argument("--evidence-json", required=True)

    p = sub.add_parser("close", help="close a verified task")
    p.add_argument("--task-id", required=True)

    p = sub.add_parser("verify-and-close", help="verify then close in one step")
    p.add_argument("--task-id", required=True)
    p.add_argument("--evidence-json", required=True)

    p = sub.add_parser("cancel", help="abandon a task, keeping its audit trail")
    p.add_argument("--task-id", required=True)
    p.add_argument("--reason", required=True)

    sub.add_parser("show-active", help="list tasks that are neither closed nor cancelled")

    p = sub.add_parser("get", help="show one task")
    p.add_argument("--task-id", required=True)

    p = sub.add_parser("history", help="show every transition of one task")
    p.add_argument("--task-id", required=True)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db_path: Path = args.db or get_default_db_path()

    try:
        if args.action == "create":
            out: Any = create_task(db_path, args.task_id, args.route, args.summary)
        elif args.action == "dispatch":
            out = dispatch_task(db_path, args.task_id, args.worker_session, args.route)
        elif args.action == "receive-result":
            out = receive_result(db_path, args.task_id, args.summary)
        elif args.action == "verify":
            out = verify_task(db_path, args.task_id, json.loads(args.evidence_json))
        elif args.action == "close":
            out = close_task(db_path, args.task_id)
        elif args.action == "verify-and-close":
            out = verify_and_close(db_path, args.task_id, json.loads(args.evidence_json))
        elif args.action == "cancel":
            out = cancel_task(db_path, args.task_id, args.reason)
        elif args.action == "show-active":
            out = list_active_tasks(db_path)
        elif args.action == "get":
            out = get_task(db_path, args.task_id)
            if out is None:
                print(f"FAIL_LOUD: task '{args.task_id}' not found", file=sys.stderr)
                return TaskNotFoundError.exit_code
        elif args.action == "history":
            out = get_history(db_path, args.task_id)
        else:  # pragma: no cover - argparse enforces the choice set
            return 2
    except json.JSONDecodeError as exc:
        print(f"FAIL_LOUD: --evidence-json is not valid JSON: {exc}", file=sys.stderr)
        return 2
    except LedgerError as exc:
        print(f"FAIL_LOUD [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return exc.exit_code

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
