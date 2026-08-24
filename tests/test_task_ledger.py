#!/usr/bin/env python3
"""Behavioural tests for core/task_ledger.py.

The ledger's whole value is what it REFUSES to record. The happy path is the
least interesting case here, so it gets one test and the refusals get the rest:
a ledger that cheerfully closes a task it has never heard of, on empty evidence,
out of order, is a status column pretending to be a state machine.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
sys.path.insert(0, str(CORE))

import task_ledger  # noqa: E402

GOOD_EVIDENCE = {"commit": "abc1234def", "tests_exit_code": 0, "diff_count": 0}


class LedgerTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ledger.sqlite"

    def tearDown(self):
        self._tmp.cleanup()

    def seed(self, task_id="t-1", through="result_received"):
        task_ledger.create_task(self.db, task_id, "finance", "monthly reconcile")
        if through == "created":
            return task_id
        task_ledger.dispatch_task(self.db, task_id, "worker-session-1")
        if through == "dispatched":
            return task_id
        task_ledger.receive_result(self.db, task_id, "reconcile completed, 0 diff")
        return task_id


class HappyPath(LedgerTestBase):
    def test_full_lifecycle_and_audit_trail(self):
        tid = self.seed()
        task_ledger.verify_task(self.db, tid, GOOD_EVIDENCE)
        result = task_ledger.close_task(self.db, tid)
        self.assertEqual(result["status"], "closed")

        self.assertEqual(task_ledger.list_active_tasks(self.db), [])
        stored = task_ledger.get_task(self.db, tid)
        self.assertEqual(stored["status"], "closed")
        self.assertEqual(json.loads(stored["evidence"])["commit"], "abc1234def")

        states = [row["to_state"] for row in task_ledger.get_history(self.db, tid)]
        self.assertEqual(
            states, ["created", "dispatched", "result_received", "verified", "closed"]
        )


class RefusesPhantomTasks(LedgerTestBase):
    """A write that matches no row must never report success."""

    def test_closing_an_unknown_task_raises(self):
        with self.assertRaises(task_ledger.TaskNotFoundError):
            task_ledger.verify_and_close(self.db, "ghost-999", GOOD_EVIDENCE)
        self.assertIsNone(task_ledger.get_task(self.db, "ghost-999"))

    def test_every_transition_rejects_unknown_task(self):
        for call in (
            lambda: task_ledger.dispatch_task(self.db, "ghost", "w"),
            lambda: task_ledger.receive_result(self.db, "ghost", "done"),
            lambda: task_ledger.verify_task(self.db, "ghost", GOOD_EVIDENCE),
            lambda: task_ledger.close_task(self.db, "ghost"),
            lambda: task_ledger.cancel_task(self.db, "ghost", "n/a"),
        ):
            with self.subTest(call=call):
                with self.assertRaises(task_ledger.TaskNotFoundError):
                    call()

    def test_duplicate_task_id_is_refused(self):
        self.seed("t-dup", through="created")
        with self.assertRaises(task_ledger.IllegalTransitionError):
            task_ledger.create_task(self.db, "t-dup", "finance", "second attempt")


class RefusesUnsupportedEvidence(LedgerTestBase):
    """Rule R2 — closing requires evidence that satisfies the contract."""

    def test_empty_evidence_is_refused_and_task_stays_open(self):
        tid = self.seed()
        with self.assertRaises(task_ledger.EvidenceRejectedError):
            task_ledger.verify_and_close(self.db, tid, {})
        self.assertEqual(task_ledger.get_task(self.db, tid)["status"], "result_received")
        self.assertEqual(len(task_ledger.list_active_tasks(self.db)), 1)

    def test_missing_commit_is_refused(self):
        tid = self.seed()
        with self.assertRaises(task_ledger.EvidenceRejectedError) as ctx:
            task_ledger.verify_task(self.db, tid, {"tests_exit_code": 0})
        self.assertIn("commit", str(ctx.exception))

    def test_stub_commit_ref_is_refused(self):
        tid = self.seed()
        with self.assertRaises(task_ledger.EvidenceRejectedError):
            task_ledger.verify_task(self.db, tid, {"commit": "abc", "tests_exit_code": 0})

    def test_red_test_suite_cannot_close_a_task(self):
        tid = self.seed()
        with self.assertRaises(task_ledger.EvidenceRejectedError) as ctx:
            task_ledger.verify_task(self.db, tid, {"commit": "abc1234def", "tests_exit_code": 1})
        self.assertIn("red suite", str(ctx.exception))

    def test_non_integer_exit_code_is_refused(self):
        tid = self.seed()
        for bad in ["0", True, None]:
            with self.subTest(value=bad):
                with self.assertRaises(task_ledger.EvidenceRejectedError):
                    task_ledger.verify_task(
                        self.db, tid, {"commit": "abc1234def", "tests_exit_code": bad}
                    )

    def test_custom_validator_is_honoured(self):
        tid = self.seed()
        strict = lambda ev: [] if ev.get("reviewed_by") else ["no independent reviewer recorded"]
        with self.assertRaises(task_ledger.EvidenceRejectedError):
            task_ledger.verify_task(self.db, tid, GOOD_EVIDENCE, validator=strict)
        task_ledger.verify_task(self.db, tid, {"reviewed_by": "dispatcher"}, validator=strict)
        self.assertEqual(task_ledger.get_task(self.db, tid)["status"], "verified")


class EnforcesStateMachine(LedgerTestBase):
    """Rule R1 — a worker's claim cannot skip the dispatcher's verification."""

    def test_cannot_close_straight_from_created(self):
        tid = self.seed("t-jump", through="created")
        with self.assertRaises(task_ledger.IllegalTransitionError):
            task_ledger.close_task(self.db, tid)
        self.assertEqual(task_ledger.get_task(self.db, tid)["status"], "created")

    def test_cannot_close_without_verification(self):
        tid = self.seed()  # result_received
        with self.assertRaises(task_ledger.IllegalTransitionError):
            task_ledger.close_task(self.db, tid)

    def test_receive_result_does_not_close(self):
        tid = self.seed()
        self.assertEqual(task_ledger.get_task(self.db, tid)["status"], "result_received")
        self.assertEqual(len(task_ledger.list_active_tasks(self.db)), 1)

    def test_terminal_states_are_terminal(self):
        tid = self.seed()
        task_ledger.verify_and_close(self.db, tid, GOOD_EVIDENCE)
        for call in (
            lambda: task_ledger.dispatch_task(self.db, tid, "w2"),
            lambda: task_ledger.receive_result(self.db, tid, "again"),
            lambda: task_ledger.cancel_task(self.db, tid, "changed my mind"),
        ):
            with self.subTest(call=call):
                with self.assertRaises(task_ledger.IllegalTransitionError):
                    call()

    def test_cancel_preserves_the_row(self):
        tid = self.seed("t-cancel", through="dispatched")
        task_ledger.cancel_task(self.db, tid, "superseded by t-2")
        row = task_ledger.get_task(self.db, tid)
        self.assertEqual(row["status"], "cancelled")
        self.assertNotIn(row, task_ledger.list_active_tasks(self.db))

    def test_transition_table_covers_every_declared_status(self):
        self.assertEqual(set(task_ledger.TRANSITIONS), set(task_ledger.VALID_STATUSES))
        for source, targets in task_ledger.TRANSITIONS.items():
            for target in targets:
                self.assertIn(target, task_ledger.VALID_STATUSES, f"{source}->{target}")


class CliExitCodes(LedgerTestBase):
    """A caller that only looks at the exit code must still learn the truth."""

    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(CORE / "task_ledger.py"), "--db", str(self.db), *argv],
            capture_output=True,
            text=True,
        )

    def test_unknown_task_exits_4(self):
        cp = self.run_cli("close", "--task-id", "ghost")
        self.assertEqual(cp.returncode, 4)
        self.assertIn("FAIL_LOUD", cp.stderr)

    def test_illegal_transition_exits_5(self):
        self.seed("t-cli", through="created")
        cp = self.run_cli("close", "--task-id", "t-cli")
        self.assertEqual(cp.returncode, 5)

    def test_rejected_evidence_exits_6(self):
        self.seed("t-cli2")
        cp = self.run_cli("verify", "--task-id", "t-cli2", "--evidence-json", "{}")
        self.assertEqual(cp.returncode, 6)
        self.assertIn("FAIL_LOUD", cp.stderr)

    def test_happy_path_exits_0(self):
        self.seed("t-cli3")
        cp = self.run_cli(
            "verify-and-close", "--task-id", "t-cli3", "--evidence-json", json.dumps(GOOD_EVIDENCE)
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["status"], "closed")


if __name__ == "__main__":
    unittest.main()
