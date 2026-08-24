#!/usr/bin/env python3
"""Unit tests for pre_tool_use_safety.sh."""
import subprocess
import unittest
from pathlib import Path


class PreToolUseSafetyTests(unittest.TestCase):
    def setUp(self):
        self.script = Path(__file__).parent.parent / "core" / "pre_tool_use_safety.sh"

    def test_safe_commands(self):
        cp = subprocess.run(["bash", str(self.script), "ls -la"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0)

        cp = subprocess.run(["bash", str(self.script), "python3 run_tests.py"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 0)

    def test_rm_intercept(self):
        cp = subprocess.run(["bash", str(self.script), "rm important.txt"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("FAIL_LOUD", cp.stderr)

        cp = subprocess.run(["bash", str(self.script), "git status; rm -rf build/"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 2)

    def test_overwrite_intercept(self):
        cp = subprocess.run(["bash", str(self.script), "cp -f src.txt dst.txt"], capture_output=True, text=True)
        self.assertEqual(cp.returncode, 2)


if __name__ == "__main__":
    unittest.main()
