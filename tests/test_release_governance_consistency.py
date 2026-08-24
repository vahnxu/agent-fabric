#!/usr/bin/env python3
"""Behavioural tests for ops/check_release_governance_consistency.sh.

This gate is the last thing standing between a private absolute path and a public
repository, so the cases that matter are the leaks it must catch. Each fixture
builds a minimal repository that would otherwise pass, plants exactly one
problem, and asserts the gate refuses.

The predecessor of this gate did the opposite: it *required* a machine-specific
string to be present in order to pass, while CONTRIBUTING.md forbade exactly that
class of string. Both cases below exist so that inversion cannot return.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "ops" / "check_release_governance_consistency.sh"

GOVERNANCE_YAML = """schema_version: 1
project: fixture
type: minimal
durable_remote: pending
public_mirror: pending
"""

GITIGNORE = ".DS_Store\n.env\n__pycache__/\n"


class FixtureRepo(unittest.TestCase):
    """A minimal repository that passes the gate, so a single planted defect is
    unambiguously the cause of any failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "ops").mkdir()
        (self.root / "docs" / "workspace").mkdir(parents=True)

        shutil.copy2(GATE, self.root / "ops" / GATE.name)
        shutil.copy2(REPO / "ops" / "enforce_agent_onboarding_gate.sh", self.root / "ops")
        shutil.copy2(REPO / "ops" / "check_negative_test_coverage.py", self.root / "ops")

        agents = (
            "# AGENTS.md — fixture\n\n"
            "Every agent must run the onboarding gate before making any change:\n\n"
            "```bash\n./ops/check_release_governance_consistency.sh\n```\n\n"
            "Work must not proceed if the gate fails.\n"
        )
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            (self.root / name).write_text(agents, encoding="utf-8")

        (self.root / "governance.yaml").write_text(GOVERNANCE_YAML, encoding="utf-8")
        (self.root / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        for doc in ("README.md", "NEW_AGENT_ONBOARDING_PROMPT.md"):
            (self.root / "docs" / "workspace" / doc).write_text(
                "Run ./ops/enforce_agent_onboarding_gate.sh first.\n", encoding="utf-8"
            )

        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.stage_all()

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True
        )

    def stage_all(self):
        self.git("add", ".")

    def plant(self, relpath, content):
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.stage_all()

    def run_gate(self):
        env = dict(os.environ, FABRIC_GATE_ROOT=str(self.root))
        return subprocess.run(
            ["bash", str(self.root / "ops" / GATE.name)],
            capture_output=True, text=True, env=env,
        )


class BaselinePasses(FixtureRepo):
    def test_clean_fixture_passes(self):
        cp = self.run_gate()
        self.assertEqual(cp.returncode, 0, f"clean fixture failed:\n{cp.stdout}\n{cp.stderr}")


class CatchesPrivateData(FixtureRepo):
    """C3 — nothing machine-specific may reach a published tree."""

    def test_absolute_macos_home_path_is_caught(self):
        self.plant("core/helper.py", 'BACKUP = "/Users/' + 'someone/repos/thing.git"\n')
        self.assertEqual(self.run_gate().returncode, 1, "an absolute home path was not caught")

    def test_absolute_linux_home_path_is_caught(self):
        self.plant("core/helper.py", 'BACKUP = "/home/' + 'someone/data"\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_ssh_url_with_user_and_host_is_caught(self):
        self.plant("ops/deploy.sh", "git push ssh://" + "someone@somebox/srv/repo.git\n")
        self.assertEqual(self.run_gate().returncode, 1)

    def test_private_hostname_suffix_is_caught(self):
        # Split so this source file does not itself contain a matchable literal —
        # the gate scans every tracked file, its own fixtures included, and adding
        # an exemption for the suite would teach contributors to add exemptions.
        self.plant("ops/deploy.sh", "scp file workstation.loc" + "al:/tmp/\n")
        self.assertEqual(self.run_gate().returncode, 1)

    def test_cloud_access_key_id_is_caught(self):
        self.plant("core/conf.py", 'KEY = "AKIA' + 'ABCDEFGHIJKLMNOP"\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_private_key_block_is_caught(self):
        self.plant("core/key.pem", "-----BEGIN RSA " + "PRIVATE KEY-----\n")
        self.assertEqual(self.run_gate().returncode, 1)

    def test_inline_credential_assignment_is_caught(self):
        self.plant("core/conf.py", 'api_key = "' + 'abcdef0123456789abcdef"\n')
        self.assertEqual(self.run_gate().returncode, 1)

    def test_reserved_example_paths_are_allowed(self):
        # Fixtures legitimately need a home-shaped path; these must not trip it,
        # otherwise contributors learn to add exemptions instead of fixing leaks.
        self.plant("tests/fixture.py", 'ARGV = "/Users/' + 'test/bin/agent --serve"\n')
        cp = self.run_gate()
        self.assertEqual(cp.returncode, 0, f"a reserved example path was flagged:\n{cp.stdout}")


class CatchesInstructionDrift(FixtureRepo):
    """C1/C2 — the runtimes must not be reading different rulebooks."""

    def test_drifted_mirror_is_caught(self):
        (self.root / "CLAUDE.md").write_text("# different rules entirely\n", encoding="utf-8")
        self.stage_all()
        cp = self.run_gate()
        self.assertEqual(cp.returncode, 1, "instruction drift was not caught")
        self.assertIn("drift detected", cp.stdout)

    def test_missing_mirror_is_caught(self):
        (self.root / "GEMINI.md").unlink()
        self.stage_all()
        self.assertEqual(self.run_gate().returncode, 1)

    def test_removing_the_onboarding_clause_is_caught(self):
        for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
            (self.root / name).write_text("# no rules here\n", encoding="utf-8")
        self.stage_all()
        self.assertEqual(self.run_gate().returncode, 1)


class CatchesDeclarationDrift(FixtureRepo):
    """C4 — a declared publication target must match the configured one."""

    def test_declared_remote_that_does_not_exist_is_caught(self):
        (self.root / "governance.yaml").write_text(
            GOVERNANCE_YAML.replace("durable_remote: pending", "durable_remote: origin"),
            encoding="utf-8",
        )
        self.stage_all()
        cp = self.run_gate()
        self.assertEqual(cp.returncode, 1, "a declared-but-absent remote was not caught")

    def test_declared_remote_mismatch_is_caught(self):
        self.git("remote", "add", "backup", "https://example.invalid/x.git")
        (self.root / "governance.yaml").write_text(
            GOVERNANCE_YAML.replace("durable_remote: pending", "durable_remote: origin"),
            encoding="utf-8",
        )
        self.stage_all()
        self.assertEqual(self.run_gate().returncode, 1)

    def test_missing_declaration_is_caught(self):
        (self.root / "governance.yaml").write_text("schema_version: 1\nproject: fixture\n", encoding="utf-8")
        self.stage_all()
        self.assertEqual(self.run_gate().returncode, 1)


if __name__ == "__main__":
    unittest.main()
