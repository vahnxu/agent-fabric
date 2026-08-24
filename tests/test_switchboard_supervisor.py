#!/usr/bin/env python3
"""Behavioural tests for core/switchboard_supervisor.py.

Rotation is a safety mechanism, so the tests that matter are the ones where a
step FAILS. The invariant under test throughout: at the end of every rotation
attempt, exactly one session is serving — never zero. A supervisor that only
works when everything goes right is a supervisor that has never done anything.
"""
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
sys.path.insert(0, str(CORE))

import switchboard_supervisor as supervisor  # noqa: E402


class FakeRuntime:
    """A scripted stand-in for a real session runtime.

    Records what actually happened so a test can assert on the sequence rather
    than on the return value alone.
    """

    def __init__(self, spawn_ok=True, handshake_after=1, promote_ok=True, retire_ok=True):
        self.spawn_ok = spawn_ok
        self.handshake_after = handshake_after  # succeed on the Nth probe; 0 = never
        self.promote_ok = promote_ok
        self.retire_ok = retire_ok
        self.probe_count = 0
        self.events = []
        self.serving = "official"  # who is answering the channel right now

    def ops(self):
        return supervisor.SessionOps(
            spawn=self.spawn, handshake=self.handshake, promote=self.promote,
            retire=self.retire, destroy=self.destroy,
        )

    def spawn(self, shadow_name):
        self.events.append(f"spawn:{shadow_name}")
        if not self.spawn_ok:
            raise RuntimeError("no capacity to start a session")
        return {"name": shadow_name, "alive": True}

    def handshake(self, handle):
        self.probe_count += 1
        ok = self.handshake_after != 0 and self.probe_count >= self.handshake_after
        self.events.append(f"probe:{self.probe_count}:{'ok' if ok else 'no'}")
        return ok

    def promote(self, handle, official_name):
        self.events.append(f"promote:{official_name}")
        if not self.promote_ok:
            raise RuntimeError("name collision")
        handle["name"] = official_name
        self.serving = "shadow"

    def retire(self, old_name):
        self.events.append(f"retire:{old_name}")
        if not self.retire_ok:
            raise RuntimeError("old process will not stop")

    def destroy(self, handle):
        self.events.append("destroy")
        handle["alive"] = False


def big_transcript(tmpdir: str, mb: int = 60) -> Path:
    path = Path(tmpdir) / "transcript.jsonl"
    with open(path, "wb") as fh:
        fh.seek(mb * 1024 * 1024)
        fh.write(b"\0")
    return path


class Measurement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_size_and_threshold(self):
        empty = Path(self.dir) / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        self.assertEqual(supervisor.check_transcript_size_mb(empty), 0.0)
        self.assertFalse(supervisor.should_rotate(empty, max_mb=10.0))

        big = big_transcript(self.dir, mb=15)
        self.assertTrue(supervisor.should_rotate(big, max_mb=10.0))
        self.assertFalse(supervisor.should_rotate(big, max_mb=50.0))

    def test_missing_transcript_measures_zero_not_crash(self):
        self.assertEqual(supervisor.check_transcript_size_mb(Path(self.dir) / "nope.jsonl"), 0.0)
        self.assertEqual(supervisor.check_transcript_size_mb(None), 0.0)

    def test_capability_matching_is_about_capability_not_vendor(self):
        argv = "/opt/bin/agent-runtime --name switchboard --channel im-bridge --poll 5"
        self.assertTrue(supervisor.is_capability_match(argv, r"--channel\s+im-bridge"))
        self.assertFalse(supervisor.is_capability_match(argv, r"--channel\s+mail-bridge"))
        self.assertFalse(supervisor.is_capability_match("", r"--channel"))


class RotationHappyPath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.jsonl = big_transcript(self._tmp.name, mb=60)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ordering_is_probe_before_retire(self):
        rt = FakeRuntime()
        result = supervisor.rotate_session("official", rt.ops(), self.jsonl, sleep=lambda _: None)
        self.assertEqual(result.outcome, "rotated")
        self.assertEqual(result.steps, ["measure", "spawn", "handshake", "promote", "retire"])
        # The critical ordering claim: nothing retires before a successful probe.
        probe_idx = next(i for i, e in enumerate(rt.events) if e.startswith("probe") and e.endswith("ok"))
        retire_idx = next(i for i, e in enumerate(rt.events) if e.startswith("retire"))
        self.assertLess(probe_idx, retire_idx, "old session retired before takeover was proven")

    def test_below_threshold_does_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            small = Path(d) / "small.jsonl"
            small.write_text("x", encoding="utf-8")
            rt = FakeRuntime()
            result = supervisor.rotate_session("official", rt.ops(), small, sleep=lambda _: None)
            self.assertEqual(result.outcome, "not_needed")
            self.assertEqual(rt.events, [], "a rotation was attempted below the threshold")

    def test_force_rotates_below_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            small = Path(d) / "small.jsonl"
            small.write_text("x", encoding="utf-8")
            rt = FakeRuntime()
            result = supervisor.rotate_session(
                "official", rt.ops(), small, force=True, sleep=lambda _: None
            )
            self.assertEqual(result.outcome, "rotated")

    def test_retries_the_handshake_before_giving_up(self):
        rt = FakeRuntime(handshake_after=3)
        result = supervisor.rotate_session(
            "official", rt.ops(), self.jsonl, attempts=3, sleep=lambda _: None
        )
        self.assertEqual(result.outcome, "rotated")
        self.assertEqual(result.handshake_attempts, 3)


class RotationFailsClosed(unittest.TestCase):
    """Every failure path must end with the old session still serving."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.jsonl = big_transcript(self._tmp.name, mb=60)

    def tearDown(self):
        self._tmp.cleanup()

    def rotate(self, rt, **kw):
        return supervisor.rotate_session(
            "official", rt.ops(), self.jsonl, sleep=lambda _: None, **kw
        )

    def test_shadow_that_will_not_start_never_touches_the_old_session(self):
        rt = FakeRuntime(spawn_ok=False)
        result = self.rotate(rt)
        self.assertEqual(result.outcome, "rolled_back")
        self.assertTrue(result.service_preserved)
        self.assertNotIn("retire:official", rt.events)
        self.assertEqual(rt.serving, "official")

    def test_shadow_that_never_answers_is_destroyed_and_old_keeps_serving(self):
        rt = FakeRuntime(handshake_after=0)
        result = self.rotate(rt, attempts=3)
        self.assertEqual(result.outcome, "rolled_back")
        self.assertEqual(result.handshake_attempts, 3)
        self.assertIn("destroy", rt.events)
        self.assertNotIn("retire:official", rt.events)
        self.assertEqual(rt.serving, "official")
        self.assertIn("keeps serving", result.reason)

    def test_handshake_that_raises_counts_as_failure_not_success(self):
        rt = FakeRuntime()
        rt.handshake = lambda handle: (_ for _ in ()).throw(RuntimeError("channel refused"))
        result = self.rotate(rt, attempts=2)
        self.assertEqual(result.outcome, "rolled_back")
        self.assertEqual(rt.serving, "official")

    def test_failed_promotion_rolls_back(self):
        rt = FakeRuntime(promote_ok=False)
        result = self.rotate(rt)
        self.assertEqual(result.outcome, "rolled_back")
        self.assertIn("destroy", rt.events)
        self.assertNotIn("retire:official", rt.events)

    def test_failed_retirement_is_not_a_rollback(self):
        # The new session is already serving. Rolling back here would be the
        # actual outage, so the supervisor must report untidiness instead.
        rt = FakeRuntime(retire_ok=False)
        result = self.rotate(rt)
        self.assertEqual(result.outcome, "retire_failed")
        self.assertTrue(result.service_preserved)
        self.assertNotIn("destroy", rt.events, "rolled back a session that was already serving")
        self.assertEqual(rt.serving, "shadow")

    def test_a_failure_to_destroy_does_not_mask_the_rollback(self):
        rt = FakeRuntime(handshake_after=0)
        rt.destroy = lambda handle: (_ for _ in ()).throw(RuntimeError("process wedged"))
        result = self.rotate(rt, attempts=1)
        self.assertEqual(result.outcome, "rolled_back")
        self.assertIn("rollback_destroy_failed", result.steps)

    def test_every_outcome_preserves_service(self):
        for rt in [
            FakeRuntime(),
            FakeRuntime(spawn_ok=False),
            FakeRuntime(handshake_after=0),
            FakeRuntime(promote_ok=False),
            FakeRuntime(retire_ok=False),
        ]:
            with self.subTest(runtime=rt.__dict__):
                self.assertTrue(self.rotate(rt, attempts=2).service_preserved)


class DeadWindowReporting(unittest.TestCase):
    def test_gap_is_reported_explicitly(self):
        window = supervisor.describe_dead_window(1000.0, 1185.5)
        self.assertEqual(window["dead_seconds"], 185.5)
        self.assertTrue(window["messages_possibly_missed"])

    def test_no_gap_is_reported_as_no_gap(self):
        window = supervisor.describe_dead_window(1000.0, 1000.0)
        self.assertEqual(window["dead_seconds"], 0.0)
        self.assertFalse(window["messages_possibly_missed"])

    def test_clock_skew_does_not_produce_a_negative_window(self):
        self.assertEqual(supervisor.describe_dead_window(1200.0, 1000.0)["dead_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
