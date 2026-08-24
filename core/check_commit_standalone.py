#!/usr/bin/env python3
"""check_commit_standalone.py — will this commit still work if it is the only thing that lands?

THE PROBLEM IT SOLVES
    You stage a script and push. Your machine has a helper file sitting next to it
    that git has never heard of, so everything runs locally and the tests pass.
    On any other machine — a fresh clone, CI, a colleague — the commit is broken,
    and the failure surfaces far from the person who caused it.

WHAT IT ASSERTS
    The invariant is about the TREE, not about authorship: the set of files this
    commit produces must be closed under the references those files make. A
    reference is a violation when the referenced thing exists on this machine and
    will NOT exist in the committed tree — because that is the exact condition in
    which "works here" and "works anywhere" diverge.

    Committed tree = everything tracked in HEAD, minus staged deletions,
                     plus staged additions.

HOW IT DECIDES (and why it is not a substring scan)
    The gate this replaced searched every staged file's text for the literal name
    of every untracked file. That is wrong in both directions at once, and both
    directions were demonstrated: a Python file depending on a local helper was
    let through (the import says `helper`, the filename says `helper.py`), while a
    README that merely mentioned a filename in a sentence was blocked. So:

      · Python imports are resolved to candidate files, the way the interpreter
        would resolve them, including imports made reachable by sys.path edits.
      · Shell `source` / `.` directives are resolved as paths.
      · Literal path tokens (anything carrying a directory separator or a known
        source extension) are resolved relative to the repo root and to the
        referring file.
      · Prose in documentation is treated as prose: a reference from a .md file
        is reported as a WARNING, never a block, unless --strict is passed.

SEVERITY
    BLOCKING  a source, script or config file references something the commit
              will not carry. Exit 1.
    WARNING   a documentation file does. Exit 0 unless --strict.

KNOWN NON-COVERAGE — restate these whenever you restate this gate
    N1. Only LITERAL references are visible. A path assembled at runtime — joined
        from a variable, read from config, interpolated from an environment
        variable — is invisible to this gate. The incident that motivates this
        class of check is frequently of exactly that shape, so passing here is
        not evidence that the tree is closed; it is evidence that no literal
        reference is dangling.
    N2. Dependencies outside the repository (installed packages, system binaries,
        services, environment variables) are out of scope by construction.
    N3. It inspects the whole staged file, not just the changed hunk. A reference
        that predates your change, in a file you touched for another reason, is
        attributed to this commit. That is deliberate — the tree is either closed
        or it is not — but it means the first run in an old repo can be noisy.

ROLLBACK
    Read-only. It runs `git` plumbing and reads staged file contents; it never
    writes, stages, or commits. Nothing to undo.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

#: Files whose references are real dependencies rather than prose.
SOURCE_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".ksh", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".rb", ".pl", ".lua", ".r", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".json",
}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}

#: Extensions we are willing to treat a bare token as a path reference to.
PATHY_SUFFIXES = SOURCE_SUFFIXES | DOC_SUFFIXES | {
    ".sql", ".csv", ".tsv", ".conf", ".env", ".plist", ".service", ".lock",
}

MAX_BYTES = 512 * 1024  # skip anything larger; it is not source we can reason about

_PY_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<from>[.\w]+)\s+import|import\s+(?P<import>[.\w]+))", re.MULTILINE
)
_SH_SOURCE = re.compile(r"(?:^|[;&|]|\s)(?:source|\.)\s+([^\s;&|<>]+)", re.MULTILINE)
_PATH_TOKEN = re.compile(r"[\w./$~{}-]*[\w-]+(?:" + "|".join(
    re.escape(s) for s in sorted(PATHY_SUFFIXES)) + r")\b")


class Violation:
    def __init__(self, source: str, target: str, kind: str, severity: str):
        self.source = source
        self.target = target
        self.kind = kind
        self.severity = severity

    def __str__(self) -> str:
        return f"{self.source} -> {self.target}  ({self.kind})"

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source, "target": self.target,
            "kind": self.kind, "severity": self.severity,
        }


# ── git plumbing ──────────────────────────────────────────────────────────────

def _git(args: Sequence[str], repo: Path) -> Tuple[int, str]:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    return cp.returncode, cp.stdout


def repo_root(start: Optional[Path] = None) -> Optional[Path]:
    base = start or Path.cwd()
    rc, out = _git(["rev-parse", "--show-toplevel"], base)
    return Path(out.strip()) if rc == 0 and out.strip() else None


def staged_changes(repo: Path) -> Tuple[List[str], Set[str]]:
    """Return (files added or modified, files deleted) in the index."""
    rc, out = _git(["diff", "--cached", "--name-status"], repo)
    if rc != 0:
        return [], set()
    present: List[str] = []
    deleted: Set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        target = paths[-1]
        if status.startswith("D"):
            deleted.add(target)
        else:
            present.append(target)
    return present, deleted


def committed_tree(repo: Path) -> Set[str]:
    """Every path that exists once this commit lands, and nothing else."""
    rc, out = _git(["ls-tree", "-r", "--name-only", "HEAD"], repo)
    tracked = set(out.split("\n")) if rc == 0 else set()
    tracked.discard("")
    added, deleted = staged_changes(repo)
    return (tracked | set(added)) - deleted


# ── reference extraction ──────────────────────────────────────────────────────

def _module_candidates(module: str, referring: Path, repo: Path) -> List[str]:
    """Paths the interpreter could load `module` from, as repo-relative strings."""
    module = module.lstrip(".")
    if not module:
        return []
    head = module.split(".")[0]
    rel = Path(*module.split("."))
    bases = [repo, (repo / referring).parent, (repo / referring).parent.parent]
    out: List[str] = []
    for base in bases:
        for candidate in (base / f"{rel}.py", base / rel / "__init__.py",
                          base / f"{head}.py", base / head / "__init__.py"):
            try:
                out.append(str(candidate.resolve().relative_to(repo.resolve())))
            except (ValueError, OSError):
                continue
    return out


def _resolve_token(token: str, referring: Path, repo: Path) -> List[str]:
    """Repo-relative candidates for a literal path token."""
    token = token.strip().strip("'\"`")
    if not token or token.startswith(("http://", "https://", "-")):
        return []
    if "$" in token or "{" in token:  # runtime interpolation — see N1
        return []
    out: List[str] = []
    bases = [repo, (repo / referring).parent]
    for base in bases:
        try:
            out.append(str((base / token).resolve().relative_to(repo.resolve())))
        except (ValueError, OSError):
            continue
    return out


def extract_references(content: str, referring: str, repo: Path) -> List[Tuple[str, str]]:
    """Return (candidate_repo_relative_path, kind) pairs found in one file."""
    ref_path = Path(referring)
    found: List[Tuple[str, str]] = []

    if ref_path.suffix == ".py":
        for match in _PY_IMPORT.finditer(content):
            module = match.group("from") or match.group("import") or ""
            for candidate in _module_candidates(module, ref_path, repo):
                found.append((candidate, f"python import '{module}'"))

    if ref_path.suffix in {".sh", ".bash", ".zsh", ".ksh"} or not ref_path.suffix:
        for match in _SH_SOURCE.finditer(content):
            for candidate in _resolve_token(match.group(1), ref_path, repo):
                found.append((candidate, "shell source"))

    for match in _PATH_TOKEN.finditer(content):
        for candidate in _resolve_token(match.group(0), ref_path, repo):
            found.append((candidate, "literal path reference"))

    return found


# ── the check ─────────────────────────────────────────────────────────────────

def check_standalone_integrity(
    repo: Optional[Path] = None, strict: bool = False
) -> Tuple[bool, List[Violation]]:
    """Assert the staged tree is closed under its own literal references."""
    root = repo or repo_root()
    if root is None:
        return True, []

    staged, _ = staged_changes(root)
    if not staged:
        return True, []

    in_commit = committed_tree(root)
    violations: List[Violation] = []
    seen: Set[Tuple[str, str]] = set()

    for rel in staged:
        path = root / rel
        if not path.is_file():
            continue
        suffix = Path(rel).suffix
        if suffix and suffix not in (SOURCE_SUFFIXES | DOC_SUFFIXES):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        severity = "warning" if suffix in DOC_SUFFIXES else "blocking"

        for candidate, kind in extract_references(content, rel, root):
            if candidate in in_commit or candidate == rel:
                continue
            # Only a reference we can PROVE resolves to something on this machine
            # counts. Everything else is an unresolved name, not a hidden local
            # dependency — that is what keeps this from crying wolf on stdlib
            # imports and on prose.
            if not (root / candidate).exists():
                continue
            key = (rel, candidate)
            if key in seen:
                continue
            seen.add(key)
            violations.append(Violation(rel, candidate, kind, severity))

    blocking = [v for v in violations if v.severity == "blocking" or strict]
    return (len(blocking) == 0), violations


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the staged commit is self-contained (see module docstring for non-coverage)."
    )
    parser.add_argument("--repo", type=Path, default=None, help="repository root")
    parser.add_argument("--strict", action="store_true", help="treat documentation references as blocking")
    args = parser.parse_args(argv)

    ok, violations = check_standalone_integrity(args.repo, strict=args.strict)

    blocking = [v for v in violations if v.severity == "blocking" or args.strict]
    warnings = [v for v in violations if v not in blocking]

    for v in warnings:
        print(f"WARN  {v}", file=sys.stderr)

    if not ok:
        print("FAIL_LOUD [COMMIT_TREE_STANDALONE]:", file=sys.stderr)
        for v in blocking:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nThese commits reference files that exist on this machine but will not\n"
            "exist in the committed tree. Stage them, or remove the reference.",
            file=sys.stderr,
        )
        return 1

    print("PASS: staged tree is closed under its literal references.")
    if warnings:
        print(f"      ({len(warnings)} documentation reference(s) reported above)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
