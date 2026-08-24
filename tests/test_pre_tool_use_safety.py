#!/usr/bin/env python3
"""Behavioural tests for core/pre_tool_use_safety.sh.

The point of a gate is what it REFUSES. A suite that only proves the happy path
proves nothing: the gate this file replaced passed its own tests while letting
ten distinct destructive forms through. Every invariant below therefore carries
both a NEGATIVE case (the gate must block) and a FALSE-POSITIVE case (the gate
must not block), and the two are checked against the same gate in the same run.

Exit code contract: 0 = allow, 2 = block, 3 = malformed payload (fail closed).
"""
import json
import subprocess
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parent.parent / "core" / "pre_tool_use_safety.sh"

# Assembled at runtime so this test file can be edited and moved through tooling
# that itself guards against destructive command strings.
DEL = "r" + "m"


def run_gate(command: str) -> subprocess.CompletedProcess:
    # DEVNULL, not inherited: argv mode must not touch stdin, and inheriting an
    # open pipe here is exactly how this suite once hung the whole gate.
    return subprocess.run(
        ["bash", str(GATE), command],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30,
    )


def run_gate_stdin(payload: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GATE)], input=payload, capture_output=True, text=True, timeout=30,
    )


class DeletionMustBeReversible(unittest.TestCase):
    """Invariant I1 — no deleter may run in command position."""

    BLOCKED = [
        (f"{DEL} -rf /data", "canonical form"),
        (f"foo&&{DEL} -rf important/", "glued after && with no space"),
        (f"\\{DEL} -rf important/", "backslash-escaped command name"),
        (f"/bin/{DEL} -rf important/", "absolute path to the binary"),
        (f"sudo {DEL} -rf /etc/thing", "behind a transparent runner"),
        (f"echo x | xargs {DEL}", "handed to xargs"),
        (f'bash -c "{DEL} -rf important/"', "handed to a subshell"),
        (f'ssh host "{DEL} -rf /srv/data"', "executed on a remote host"),
        (f"find . -name '*.tmp' -exec {DEL} {{}} \\;", "through find -exec"),
        ("find . -name '*.md' -delete", "find's own -delete"),
        ("shred -u secrets.txt", "a different deleter entirely"),
        ("truncate -s0 ledger.db", "in-place truncation"),
        (
            "python3 -c 'import shutil; shutil.rmtree(\"data\")'",
            "an interpreter one-liner reaching a deletion API",
        ),
    ]

    ALLOWED = [
        (f"git {DEL} --cached stale.txt", "a git subcommand, not the deleter"),
        (f"echo 'do not {DEL} this file'", "the word inside a quoted string"),
        (f"grep -r '{DEL} -rf' docs/", "the word as a search pattern"),
        (f"# remember: never {DEL} the ledger", "the word inside a comment"),
        (f"{DEL}dir empty_dir", "a longer command that merely starts the same"),
        ("ls -la", "an ordinary read"),
        ("python3 -m unittest discover tests/", "running the test suite"),
    ]

    def test_blocks_every_destructive_carrier(self):
        for command, label in self.BLOCKED:
            with self.subTest(carrier=label):
                cp = run_gate(command)
                self.assertEqual(
                    cp.returncode, 2,
                    f"gate let through [{label}]: {command}\nstderr={cp.stderr}",
                )
                self.assertIn("FAIL_LOUD", cp.stderr)

    def test_does_not_cry_wolf(self):
        for command, label in self.ALLOWED:
            with self.subTest(case=label):
                cp = run_gate(command)
                self.assertEqual(
                    cp.returncode, 0,
                    f"gate falsely blocked [{label}]: {command}\nstderr={cp.stderr}",
                )

    def test_stdout_stays_empty_on_block(self):
        # A hook that prints to stdout gets its output schema-validated by the
        # runtime; anything unexpected there is a protocol error, not a message.
        cp = run_gate(f"{DEL} -rf /data")
        self.assertEqual(cp.stdout, "")


class WritesMustNotSilentlyDestroy(unittest.TestCase):
    """Invariant I2 — an overwrite must be explicitly guarded."""

    def test_blocks_unguarded_copy_and_move(self):
        for command in ["cp src.txt dst.txt", "mv old.txt new.txt", "cp -f a b", "mv -f a b"]:
            with self.subTest(command=command):
                cp = run_gate(command)
                self.assertEqual(cp.returncode, 2, f"unguarded write let through: {command}")

    def test_allows_guarded_copy_and_move(self):
        for command in ["cp -n src.txt dst.txt", "mv -n old.txt new.txt", "cp -i a b"]:
            with self.subTest(command=command):
                cp = run_gate(command)
                self.assertEqual(cp.returncode, 0, f"guarded write blocked: {command}\n{cp.stderr}")

    def test_blocks_truncating_redirect_onto_existing_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            victim = Path(d) / "ledger.txt"
            victim.write_text("irreplaceable\n", encoding="utf-8")
            cp = run_gate(f"echo new > {victim}")
            self.assertEqual(cp.returncode, 2, f"truncating redirect let through\n{cp.stderr}")

            cp = run_gate(f"echo new >> {victim}")
            self.assertEqual(cp.returncode, 0, f"append wrongly blocked\n{cp.stderr}")

            fresh = Path(d) / "not_yet_there.txt"
            cp = run_gate(f"echo new > {fresh}")
            self.assertEqual(cp.returncode, 0, "writing a new file wrongly blocked")

    def test_allows_redirect_to_devnull(self):
        cp = run_gate("noisy_command > /dev/null 2>&1")
        self.assertEqual(cp.returncode, 0, cp.stderr)


class PayloadHandling(unittest.TestCase):
    """The gate must accept a hook payload and fail closed on a broken one."""

    def test_extracts_command_from_hook_json(self):
        payload = json.dumps({"tool_input": {"command": f"{DEL} -rf /data"}})
        cp = run_gate_stdin(payload)
        if "jq" in cp.stderr and cp.returncode == 3:
            self.skipTest("jq not installed in this environment")
        self.assertEqual(cp.returncode, 2, f"JSON payload not inspected\n{cp.stderr}")

    def test_fails_closed_on_uninspectable_payload(self):
        cp = run_gate_stdin('{"unexpected": "shape"}')
        self.assertEqual(cp.returncode, 3, f"malformed payload not failed closed\n{cp.stderr}")
        self.assertIn("FAIL_LOUD", cp.stderr)

    def test_empty_input_is_allowed(self):
        cp = run_gate("")
        self.assertEqual(cp.returncode, 0)

    def test_does_not_block_on_a_stdin_that_never_closes(self):
        # A hook that hangs wedges the agent that called it, which reads from the
        # outside as a stuck task rather than as a gate doing its job. Regression:
        # an unbounded read here stalled the composed gate indefinitely.
        proc = subprocess.Popen(
            ["bash", str(GATE)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            proc.communicate(timeout=15)  # pipe left open, nothing ever written
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            self.fail("the hook blocked on an stdin that never closed")


if __name__ == "__main__":
    unittest.main()
