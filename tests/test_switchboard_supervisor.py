#!/usr/bin/env python3
"""Unit tests for switchboard_supervisor.py."""
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import switchboard_supervisor as supervisor


class SwitchboardSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.jsonl_path = Path(self.temp_dir.name) / "transcript.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_transcript_size_and_rotation(self):
        # 1. Empty file
        self.jsonl_path.write_text("", encoding="utf-8")
        self.assertFalse(supervisor.should_rotate(self.jsonl_path, max_mb=10.0))
        self.assertEqual(supervisor.check_transcript_size_mb(self.jsonl_path), 0.0)

        # 2. Large file (simulate 15MB)
        with open(self.jsonl_path, "wb") as f:
            f.seek(15 * 1024 * 1024)
            f.write(b"\0")

        self.assertTrue(supervisor.should_rotate(self.jsonl_path, max_mb=10.0))
        self.assertFalse(supervisor.should_rotate(self.jsonl_path, max_mb=50.0))

    def test_capability_matching(self):
        cmd = "/Users/test/bin/claude --name switchboard --channels plugin:telegram@claude-plugins-official"
        self.assertTrue(supervisor.is_capability_match(cmd, r"--channels\s+plugin:telegram"))
        self.assertFalse(supervisor.is_capability_match(cmd, r"--channels\s+plugin:discord"))


if __name__ == "__main__":
    unittest.main()
