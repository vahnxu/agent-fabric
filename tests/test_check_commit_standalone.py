#!/usr/bin/env python3
"""Behavioural tests for core/check_commit_standalone.py.

Each test builds a throwaway git repository and stages a real commit, because the
only question worth asking of this gate is what it does to a tree that is
genuinely broken — and the gate it replaced answered that question wrongly in
both directions while its own test suite stayed green.

The three cases at the top are the exact ones that were demonstrated against the
previous implementation: two true dependencies it missed, one prose mention it
falsely blocked.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parent.parent / "core"
sys.path.insert(0, str(CORE))

import check_commit_standalone as gate  # noqa: E402


class RepoFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        # A base commit so HEAD exists and ls-tree has something to say.
        (self.repo / "base.txt").write_text("base\n", encoding="utf-8")
        self.git("add", "base.txt")
        self.git("commit", "-q", "-m", "base")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True
        )

    def write(self, rel, content):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def check(self, strict=False):
        return gate.check_standalone_integrity(self.repo, strict=strict)


class CatchesRealDependencies(RepoFixture):
    """The cases the substring-scanning predecessor let through."""

    def test_python_import_of_an_unstaged_local_module_is_blocked(self):
        # The import says `helper_lib`; the file is `helper_lib.py`. A literal
        # substring scan never matches, which is exactly how this got through.
        self.write("helper_lib.py", "VALUE = 1\n")
        self.write("main.py", "from helper_lib import VALUE\nprint(VALUE)\n")
        self.git("add", "main.py")
        ok, violations = self.check()
        self.assertFalse(ok, "unstaged local module dependency was not caught")
        self.assertTrue(any(v.target == "helper_lib.py" for v in violations))
        self.assertTrue(all(v.severity == "blocking" for v in violations))

    def test_package_import_of_an_unstaged_local_package_is_blocked(self):
        self.write("pkg/__init__.py", "")
        self.write("pkg/thing.py", "X = 2\n")
        self.write("app.py", "import pkg.thing\n")
        self.git("add", "app.py")
        ok, _ = self.check()
        self.assertFalse(ok)

    def test_shell_source_of_an_unstaged_file_is_blocked(self):
        self.write("lib/common.sh", "echo shared\n")
        self.write("run.sh", "#!/usr/bin/env bash\nsource lib/common.sh\n")
        self.git("add", "run.sh")
        ok, violations = self.check()
        self.assertFalse(ok)
        self.assertTrue(any("source" in v.kind for v in violations))

    def test_config_reference_to_an_unstaged_file_is_blocked(self):
        self.write("scripts/deploy.sh", "echo deploy\n")
        self.write(".ci.yml", "steps:\n  - run: bash scripts/deploy.sh\n")
        self.git("add", ".ci.yml")
        ok, _ = self.check()
        self.assertFalse(ok)

    def test_gitignored_dependency_is_still_a_violation(self):
        # Ignored is not the same as present. A fresh clone will not have it.
        self.write(".gitignore", "secrets_lib.py\n")
        self.write("secrets_lib.py", "TOKEN = 'x'\n")
        self.write("main.py", "import secrets_lib\n")
        self.git("add", "main.py")
        ok, _ = self.check()
        self.assertFalse(ok, "a gitignored local dependency was treated as present")


class DoesNotCryWolf(RepoFixture):
    """The case the predecessor blocked but should not have."""

    def test_prose_mention_in_documentation_does_not_block(self):
        self.write("notes.txt", "scratch\n")
        self.write("README.md", "See notes.txt for context.\n")
        self.git("add", "README.md")
        ok, violations = self.check()
        self.assertTrue(ok, "a prose mention blocked the commit")
        self.assertTrue(all(v.severity == "warning" for v in violations))

    def test_strict_mode_promotes_documentation_references(self):
        self.write("notes.txt", "scratch\n")
        self.write("README.md", "See notes.txt for context.\n")
        self.git("add", "README.md")
        ok, _ = self.check(strict=True)
        self.assertFalse(ok, "--strict did not promote the documentation warning")

    def test_stdlib_and_third_party_imports_are_not_flagged(self):
        self.write("main.py", "import os\nimport json\nimport numpy\nfrom pathlib import Path\n")
        self.git("add", "main.py")
        ok, violations = self.check()
        self.assertTrue(ok, f"flagged a non-local import: {[str(v) for v in violations]}")
        self.assertEqual(violations, [])

    def test_reference_to_a_file_in_the_same_commit_is_fine(self):
        self.write("helper_lib.py", "VALUE = 1\n")
        self.write("main.py", "from helper_lib import VALUE\n")
        self.git("add", "helper_lib.py", "main.py")
        ok, _ = self.check()
        self.assertTrue(ok, "a dependency staged in the same commit was flagged")

    def test_reference_to_an_already_tracked_file_is_fine(self):
        self.write("helper_lib.py", "VALUE = 1\n")
        self.git("add", "helper_lib.py")
        self.git("commit", "-q", "-m", "add helper")
        self.write("main.py", "from helper_lib import VALUE\n")
        self.git("add", "main.py")
        ok, _ = self.check()
        self.assertTrue(ok)

    def test_runtime_interpolated_path_is_not_guessed_at(self):
        # Non-coverage N1, asserted so it stays a documented limit rather than
        # quietly becoming a false-positive machine.
        self.write("config.yml", "x\n")
        self.write("main.py", 'p = f"{base_dir}/config.yml"\n')
        self.git("add", "main.py")
        ok, _ = self.check()
        self.assertTrue(ok)

    def test_empty_index_passes(self):
        ok, violations = self.check()
        self.assertTrue(ok)
        self.assertEqual(violations, [])


class DeletionAwareness(RepoFixture):
    def test_referencing_a_file_this_commit_deletes_is_blocked(self):
        self.write("helper_lib.py", "VALUE = 1\n")
        self.write("main.py", "from helper_lib import VALUE\n")
        self.git("add", "helper_lib.py", "main.py")
        self.git("commit", "-q", "-m", "both")
        # Now delete the dependency in the index while keeping it on disk.
        self.git("rm", "-q", "--cached", "helper_lib.py")
        self.write("main.py", "from helper_lib import VALUE  # touched\n")
        self.git("add", "main.py")
        ok, violations = self.check()
        self.assertFalse(ok, "a staged deletion of a dependency was not caught")
        self.assertTrue(any(v.target == "helper_lib.py" for v in violations))


class CliContract(RepoFixture):
    def run_cli(self, *argv):
        return subprocess.run(
            [sys.executable, str(CORE / "check_commit_standalone.py"),
             "--repo", str(self.repo), *argv],
            capture_output=True, text=True,
        )

    def test_exit_1_and_fail_loud_on_violation(self):
        self.write("helper_lib.py", "VALUE = 1\n")
        self.write("main.py", "from helper_lib import VALUE\n")
        self.git("add", "main.py")
        cp = self.run_cli()
        self.assertEqual(cp.returncode, 1)
        self.assertIn("FAIL_LOUD", cp.stderr)
        self.assertIn("helper_lib.py", cp.stderr)

    def test_exit_0_on_clean_tree(self):
        self.write("main.py", "import os\n")
        self.git("add", "main.py")
        cp = self.run_cli()
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("PASS", cp.stdout)

    def test_module_annotations_are_resolvable(self):
        # The predecessor annotated a return type it never imported; it only
        # survived because postponed evaluation hid the NameError.
        import typing
        typing.get_type_hints(gate.check_standalone_integrity)


if __name__ == "__main__":
    unittest.main()
