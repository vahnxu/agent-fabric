#!/usr/bin/env python3
"""Unit tests for check_commit_standalone.py."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import check_commit_standalone


class CheckCommitStandaloneTests(unittest.TestCase):
    def test_imports_and_interface(self):
        ok, violations = check_commit_standalone.check_standalone_integrity()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(violations, list)


if __name__ == "__main__":
    unittest.main()
