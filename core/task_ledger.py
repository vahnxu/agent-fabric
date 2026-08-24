#!/usr/bin/env python3
"""task_ledger.py: Universal Task State Machine Ledger for Agent Operations Fabric.

Manages the lifecycle of dispatched agent tasks without relying on model memory:
created -> dispatched -> result_received -> verified -> closed
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

VALID_STATUSES = ["created", "dispatched", "result_received", "verified", "closed", "cancelled"]


def get_default_db_path() -> Path:
    state_dir = Path(os.environ.get("FABRIC_STATE_DIR", Path.home() / ".local" / "state" / "agent_fabric"))
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "task_ledger.sqlite"


def init_db(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_ledger (
                task_id TEXT PRIMARY KEY,
                route TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT,
                worker_session TEXT,
                evidence JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def create_task(db_path: Path, task_id: str, route: str, summary: str) -> Dict[str, Any]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO task_ledger (task_id, route, status, summary)
            VALUES (?, ?, 'created', ?)
            ON CONFLICT(task_id) DO UPDATE SET
                route=excluded.route,
                summary=excluded.summary,
                updated_at=CURRENT_TIMESTAMP
        """, (task_id, route, summary))
        conn.commit()
    return {"task_id": task_id, "route": route, "status": "created", "summary": summary}


def dispatch_task(db_path: Path, task_id: str, worker_session: str, route: Optional[str] = None, summary: Optional[str] = None) -> Dict[str, Any]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO task_ledger (task_id, route, status, summary, worker_session)
            VALUES (?, COALESCE(?, 'default'), 'dispatched', ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status='dispatched',
                worker_session=excluded.worker_session,
                summary=COALESCE(excluded.summary, task_ledger.summary),
                route=COALESCE(excluded.route, task_ledger.route),
                updated_at=CURRENT_TIMESTAMP
        """, (task_id, route, summary, worker_session))
        conn.commit()
    return {"task_id": task_id, "status": "dispatched", "worker_session": worker_session}


def receive_result(db_path: Path, task_id: str, result_summary: str) -> Dict[str, Any]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            UPDATE task_ledger
            SET status='result_received', summary=?, updated_at=CURRENT_TIMESTAMP
            WHERE task_id=?
        """, (result_summary, task_id))
        conn.commit()
    return {"task_id": task_id, "status": "result_received", "summary": result_summary}


def verify_and_close(db_path: Path, task_id: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    init_db(db_path)
    evidence_json = json.dumps(evidence)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            UPDATE task_ledger
            SET status='closed', evidence=?, updated_at=CURRENT_TIMESTAMP
            WHERE task_id=?
        """, (evidence_json, task_id))
        conn.commit()
    return {"task_id": task_id, "status": "closed", "evidence": evidence}


def list_active_tasks(db_path: Path) -> List[Dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT task_id, route, status, summary, worker_session, updated_at
            FROM task_ledger
            WHERE status NOT IN ('closed', 'cancelled')
            ORDER BY updated_at DESC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_task(db_path: Path, task_id: str) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM task_ledger WHERE task_id=?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent Operations Fabric Task Ledger")
    parser.add_argument("--db", type=Path, default=get_default_db_path(), help="Path to sqlite DB")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # create
    p_create = subparsers.add_parser("create", help="Create a new task")
    p_create.add_argument("--task-id", required=True)
    p_create.add_argument("--route", default="general")
    p_create.add_argument("--summary", default="")

    # dispatch
    p_disp = subparsers.add_parser("dispatch", help="Dispatch task to worker session")
    p_disp.add_argument("--task-id", required=True)
    p_disp.add_argument("--worker-session", required=True)
    p_disp.add_argument("--route", default=None)
    p_disp.add_argument("--summary", default=None)

    # receive-result
    p_res = subparsers.add_parser("receive-result", help="Record result from worker")
    p_res.add_argument("--task-id", required=True)
    p_res.add_argument("--summary", required=True)

    # verify-and-close
    p_close = subparsers.add_parser("verify-and-close", help="Audit evidence and close task")
    p_close.add_argument("--task-id", required=True)
    p_close.add_argument("--evidence-json", required=True, help="JSON string with commit/test verification")

    # show-active
    subparsers.add_parser("show-active", help="Show active tasks")

    # get
    p_get = subparsers.add_parser("get", help="Get single task")
    p_get.add_argument("--task-id", required=True)

    args = parser.parse_args()
    db_path = args.db

    if args.action == "create":
        res = create_task(db_path, args.task_id, args.route, args.summary)
        print(json.dumps(res, indent=2))
    elif args.action == "dispatch":
        res = dispatch_task(db_path, args.task_id, args.worker_session, args.route, args.summary)
        print(json.dumps(res, indent=2))
    elif args.action == "receive-result":
        res = receive_result(db_path, args.task_id, args.summary)
        print(json.dumps(res, indent=2))
    elif args.action == "verify-and-close":
        evidence = json.loads(args.evidence_json)
        res = verify_and_close(db_path, args.task_id, evidence)
        print(json.dumps(res, indent=2))
    elif args.action == "show-active":
        active = list_active_tasks(db_path)
        print(json.dumps(active, indent=2))
    elif args.action == "get":
        t = get_task(db_path, args.task_id)
        print(json.dumps(t, indent=2) if t else "Task not found")

    return 0


if __name__ == "__main__":
    sys.exit(main())
