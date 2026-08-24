#!/usr/bin/env python3
"""Behavioural tests for ops/check_negative_test_coverage.py.

The meta-gate is held to its own standard: the cases that matter are the ones
where it must FAIL. Each fixture below reproduces one of the shapes that actually
shipped green in this repository before the gate existed.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent / "ops"
sys.path.insert(0, str(OPS))

import check_negative_test_coverage as meta  # noqa: E402

HAPPY_PATH_ONLY = '''
import unittest
class T(unittest.TestCase):
    def test_it_runs(self):
        self.assertTrue(True)
        self.assertEqual(1, 1)
        self.assertIsInstance([], list)
'''

WITH_REFUSALS = '''
import unittest
class T(unittest.TestCase):
    def test_it_refuses(self):
        with self.assertRaises(ValueError):
            raise ValueError()
        self.assertFalse(False)
        self.assertNotEqual(1, 2)
'''

GATE_WITH_LIMITS = '''#!/usr/bin/env python3
"""check_thing.py

KNOWN NON-COVERAGE
    N1. Does not resolve indirection.
"""
'''

GATE_WITHOUT_LIMITS = '''#!/usr/bin/env python3
"""check_thing.py — asserts a thing."""
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        (self.repo / "core").mkdir()
        (self.repo / "tests").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def add_module(self, name, body="x = 1\n"):
        (self.repo / "core" / name).write_text(body, encoding="utf-8")

    def add_suite(self, stem, body):
        (self.repo / "tests" / f"test_{stem}.py").write_text(body, encoding="utf-8")

    def audit(self, **kw):
        return meta.audit(self.repo, **kw)


class RefusesInadequateSuites(Fixture):
    def test_module_with_no_suite_at_all_fails(self):
        self.add_module("worker.py")
        failures, report = self.audit()
        self.assertTrue(failures)
        self.assertIn("has no suite", failures[0])
        self.assertIsNone(report[0]["suite"])

    def test_happy_path_only_suite_fails(self):
        # This is the exact shape that let four broken mechanisms ship green.
        self.add_module("worker.py")
        self.add_suite("worker", HAPPY_PATH_ONLY)
        failures, _ = self.audit()
        self.assertTrue(failures, "a happy-path-only suite was accepted")
        self.assertIn("does not demonstrate", failures[0])

    def test_gate_without_documented_limits_fails(self):
        self.add_module("check_thing.py", GATE_WITHOUT_LIMITS)
        self.add_suite("check_thing", WITH_REFUSALS)
        failures, _ = self.audit()
        self.assertTrue(failures)
        self.assertIn("non-coverage", failures[0])

    def test_empty_core_directory_fails_rather_than_passing_vacuously(self):
        failures, _ = self.audit()
        self.assertTrue(failures, "an empty core/ passed vacuously")

    def test_shell_module_is_held_to_the_same_bar(self):
        self.add_module("worker.sh", "#!/bin/sh\nexit 0\n")
        failures, _ = self.audit()
        self.assertTrue(failures, "a shell mechanism escaped the requirement")


class AcceptsAdequateSuites(Fixture):
    def test_suite_with_refusals_passes(self):
        self.add_module("worker.py")
        self.add_suite("worker", WITH_REFUSALS)
        failures, report = self.audit()
        self.assertEqual(failures, [])
        self.assertGreaterEqual(report[0]["negative_assertions"], 3)

    def test_gate_with_documented_limits_passes(self):
        self.add_module("check_thing.py", GATE_WITH_LIMITS)
        self.add_suite("check_thing", WITH_REFUSALS)
        failures, _ = self.audit()
        self.assertEqual(failures, [])

    def test_threshold_is_configurable(self):
        self.add_module("worker.py")
        self.add_suite("worker", WITH_REFUSALS)
        self.assertEqual(self.audit(min_negative=3)[0], [])
        self.assertTrue(self.audit(min_negative=99)[0])

    def test_nonzero_exit_code_assertions_count_as_negative(self):
        self.add_module("worker.py")
        self.add_suite("worker", '''
import unittest
class T(unittest.TestCase):
    def test_blocks(self):
        self.assertEqual(cp.returncode, 2)
        self.assertEqual(cp.returncode, 1)
        self.assertEqual(cp.returncode, 6)
''')
        failures, _ = self.audit()
        self.assertEqual(failures, [], "exit-code refusals were not recognised")


class LiveRepositoryHoldsTheLine(unittest.TestCase):
    def test_this_repository_passes_its_own_gate(self):
        cp = subprocess.run(
            [sys.executable, str(OPS / "check_negative_test_coverage.py")],
            capture_output=True, text=True,
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
