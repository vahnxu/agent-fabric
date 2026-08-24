#!/usr/bin/env python3
"""Unit tests for task_ledger.py."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import task_ledger


class TaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_ledger.sqlite"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_task_lifecycle(self):
        # 1. create
        res = task_ledger.create_task(self.db_path, "task-001", "finance", "Monthly reconcile")
        self.assertEqual(res["status"], "created")
        self.assertEqual(res["task_id"], "task-001")

        # 2. dispatch
        res = task_ledger.dispatch_task(self.db_path, "task-001", "session-worker-1")
        self.assertEqual(res["status"], "dispatched")
        self.assertEqual(res["worker_session"], "session-worker-1")

        # 3. receive result
        res = task_ledger.receive_result(self.db_path, "task-001", "Reconcile completed with 0 diff")
        self.assertEqual(res["status"], "result_received")

        # 4. verify and close
        evidence = {"commit": "abc1234", "tests": "pass", "diff_count": 0}
        res = task_ledger.verify_and_close(self.db_path, "task-001", evidence)
        self.assertEqual(res["status"], "closed")
        self.assertEqual(res["evidence"]["commit"], "abc1234")

        # verify active list is empty
        active = task_ledger.list_active_tasks(self.db_path)
        self.assertEqual(len(active), 0)

        # verify task details
        task = task_ledger.get_task(self.db_path, "task-001")
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "closed")


if __name__ == "__main__":
    unittest.main()
