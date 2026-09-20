#!/usr/bin/env python3
"""Behavioural tests for ops/install_git_hooks.sh.

An unarmed gate is the failure this installer exists to prevent, so the tests
that matter are: does --check actually report an unarmed repository as unarmed,
and does the armed hook actually refuse a commit that carries private content.
Asserting only that the installer writes a file would reproduce the exact defect
this repository is recovering from — proof that something ran, not that it works.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "ops" / "install_git_hooks.sh"
GATE = REPO / "ops" / "check_release_governance_consistency.sh"

GOVERNANCE_YAML = """schema_version: 1
project: fixture
type: minimal
visibility: public
durable_remote: pending
"""

AGENTS = (
    "# AGENTS.md — fixture\n\n"
    "Arm the hooks, then run the gates before making any change:\n\n"
    "```bash\n./ops/install_git_hooks.sh --apply\n"
    "./ops/check_release_governance_consistency.sh\n"
    "./ops/enforce_agent_onboarding_gate.sh\n```\n\n"
    "Work must not proceed if any gate fails.\n"
)


class HookFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ops").mkdir()
        (self.root / "docs" / "workspace").mkdir(parents=True)

        for script in (INSTALLER, GATE, REPO / "ops" / "enforce_agent_onboarding_gate.sh"):
            shutil.copy2(script, self.root / "ops" / script.name)
        shutil.copy2(REPO / "ops" / "check_negative_test_coverage.py", self.root / "ops")

        (self.root / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.root / "GEMINI.md").symlink_to("AGENTS.md")
        (self.root / "governance.yaml").write_text(GOVERNANCE_YAML, encoding="utf-8")
        (self.root / ".gitignore").write_text(".DS_Store\n.env\n__pycache__/\nAGENTS.md\nCLAUDE.md\nGEMINI.md\n", encoding="utf-8")
        for doc in ("README.md", "NEW_AGENT_ONBOARDING_PROMPT.md"):
            (self.root / "docs" / "workspace" / doc).write_text(
                "Run ./ops/enforce_agent_onboarding_gate.sh first.\n", encoding="utf-8"
            )

        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True
        )

    def installer(self, *args):
        return subprocess.run(
            ["bash", str(self.root / "ops" / "install_git_hooks.sh"), *args],
            capture_output=True, text=True, cwd=str(self.root),
        )


class ReportsUnarmedRepositories(HookFixture):
    def test_check_fails_before_install(self):
        cp = self.installer("--check")
        self.assertEqual(cp.returncode, 1, "an unarmed repository reported as armed")
        self.assertIn("not armed", cp.stderr)

    def test_check_fails_when_hook_is_not_executable(self):
        self.installer("--apply")
        hook = self.root / ".git" / "hooks" / "pre-commit"
        hook.chmod(0o644)
        self.assertEqual(self.installer("--check").returncode, 1, "a non-executable hook passed")

    def test_check_fails_when_hook_is_a_foreign_script(self):
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        self.assertEqual(self.installer("--check").returncode, 1, "someone else's hook passed as ours")


class ArmedHookRefusesLeakingCommits(HookFixture):
    def test_apply_then_check_passes(self):
        self.assertEqual(self.installer("--apply").returncode, 0)
        cp = self.installer("--check")
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_injected_mirror_block_is_refused_at_commit_time(self):
        self.installer("--apply")
        target = self.root / "AGENTS.md"
        target.write_text(
            target.read_text(encoding="utf-8")
            + "\n<!-- BEGIN NON_CLAUDE" + "_L2_MIRROR -->\nprivate governance\n",
            encoding="utf-8",
        )
        self.git("add", "-f", "AGENTS.md")
        head_before = self.git("rev-parse", "HEAD").stdout.strip()
        cp = self.git("commit", "-m", "should be refused")
        self.assertNotEqual(cp.returncode, 0, "the injected commit was allowed")
        self.assertEqual(
            self.git("rev-parse", "HEAD").stdout.strip(), head_before,
            "HEAD moved despite the hook refusing",
        )

    def test_machine_identity_is_refused_at_commit_time(self):
        self.installer("--apply")
        note = self.root / "docs" / "workspace" / "note.md"
        note.write_text("pushed to the Mac mi" + "ni bare mirror\n", encoding="utf-8")
        self.git("add", "docs/workspace/note.md")
        head_before = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("commit", "-m", "should be refused")
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), head_before)

    def test_clean_commit_still_goes_through(self):
        # A gate that blocks ordinary work gets uninstalled, and an uninstalled
        # gate protects nothing.
        self.installer("--apply")
        (self.root / "docs" / "workspace" / "fine.md").write_text("ordinary\n", encoding="utf-8")
        self.git("add", "docs/workspace/fine.md")
        cp = self.git("commit", "-m", "ordinary change")
        self.assertEqual(cp.returncode, 0, f"a clean commit was blocked:\n{cp.stdout}\n{cp.stderr}")

    def test_existing_foreign_hook_is_preserved_not_destroyed(self):
        hooks = self.root / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "pre-commit").write_text("#!/bin/sh\necho someone-elses\n", encoding="utf-8")
        cp = self.installer("--apply")
        self.assertEqual(cp.returncode, 0)
        backups = list(hooks.glob("pre-commit.pre-agent-fabric.*"))
        self.assertTrue(backups, "the pre-existing hook was overwritten without a backup")
        self.assertIn("someone-elses", backups[0].read_text(encoding="utf-8"))


class LiveRepositoryIsArmed(unittest.TestCase):
    def test_this_checkout_has_the_hook_installed(self):
        cp = subprocess.run(
            ["bash", str(INSTALLER), "--check"], capture_output=True, text=True
        )
        if cp.returncode != 0:
            self.skipTest("hooks are per-checkout; run ./ops/install_git_hooks.sh --apply")
        self.assertIn("armed", cp.stdout)


if __name__ == "__main__":
    unittest.main()
