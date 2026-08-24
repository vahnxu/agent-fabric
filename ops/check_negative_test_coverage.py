#!/usr/bin/env python3
"""check_negative_test_coverage.py — a gate must prove what it REFUSES.

WHY THIS GATE EXISTS
    Every mechanism in core/ exists to refuse something. A test suite that only
    exercises the accepted path therefore tests the one behaviour nobody depends
    on, and it goes green whether or not the refusal works at all. That is not a
    hypothetical: this repository once shipped four core modules whose suites
    were entirely happy-path. All four passed. Three of the four refused nothing
    — one blocked a single syntactic form out of eleven destructive ones, one
    closed tasks on empty evidence, one reported success for work it had never
    heard of — and the green suite is precisely why nobody noticed.

WHAT IT ASSERTS
    I1. Every module in core/ has a corresponding suite in tests/.
    I2. That suite contains real NEGATIVE assertions — assertions that a refusal
        happened — not merely assertions that a call returned.
    I3. Every module whose job is to refuse (a gate: check_* / *_safety / *_guard)
        documents its own non-coverage, so that restating the gate downstream
        cannot silently drop the limits. A gate believed to cover more than it
        does is more dangerous than no gate.

    Asserting the invariant, not a list of known-bad modules: a new core module
    added tomorrow is held to the same bar without anyone editing this file.

FAILURE MODES
    Any violation exits 1 with the module named and the missing evidence stated.
    Nothing is written; this gate is read-only.

TUNING
    --min-negative raises the required count. The default of 3 is deliberately
    low: the point is to make "I wrote no refusal test at all" impossible, not to
    legislate suite size.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_MIN_NEGATIVE = 3

#: Assertion forms that can only be satisfied by an actual refusal.
NEGATIVE_ASSERTIONS = [
    re.compile(r"\bassertRaises(Regex)?\b"),
    re.compile(r"\bassertFalse\s*\("),
    re.compile(r"\bassertNotEqual\s*\("),
    re.compile(r"\bassertIn\s*\(\s*['\"]FAIL_LOUD"),
    # an exit code compared against a non-zero literal
    re.compile(r"returncode\s*,\s*[1-9]\d*"),
    re.compile(r"assertEqual\s*\(\s*[^,]*returncode\s*,\s*[1-9]\d*"),
    re.compile(r"\bpytest\.raises\b"),
]

#: Modules whose entire purpose is refusal; these must document their limits.
GATE_NAME = re.compile(r"(^check_|_safety$|_guard$|^enforce_)")

NON_COVERAGE_MARKER = re.compile(
    r"(KNOWN NON-COVERAGE|NON-COVERAGE|Known non-coverage|不覆盖)", re.IGNORECASE
)

SKIP_STEMS = {"__init__"}


def module_stem(path: Path) -> str:
    return path.stem


def find_core_modules(core_dir: Path) -> List[Path]:
    if not core_dir.is_dir():
        return []
    out = []
    for child in sorted(core_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix not in {".py", ".sh", ".bash"}:
            continue
        if module_stem(child) in SKIP_STEMS:
            continue
        out.append(child)
    return out


def locate_suite(tests_dir: Path, stem: str) -> Optional[Path]:
    candidate = tests_dir / f"test_{stem}.py"
    return candidate if candidate.is_file() else None


def count_negative_assertions(source: str) -> int:
    return sum(len(pattern.findall(source)) for pattern in NEGATIVE_ASSERTIONS)


def audit(
    repo: Path, min_negative: int = DEFAULT_MIN_NEGATIVE
) -> Tuple[List[str], List[Dict[str, object]]]:
    core_dir = repo / "core"
    tests_dir = repo / "tests"
    failures: List[str] = []
    report: List[Dict[str, object]] = []

    modules = find_core_modules(core_dir)
    if not modules:
        failures.append(f"no core modules found under {core_dir}")
        return failures, report

    for module in modules:
        stem = module_stem(module)
        row: Dict[str, object] = {"module": f"core/{module.name}"}

        suite = locate_suite(tests_dir, stem)
        if suite is None:
            failures.append(
                f"core/{module.name} has no suite at tests/test_{stem}.py — "
                "a mechanism with no test cannot be shown to work at all"
            )
            row.update({"suite": None, "negative_assertions": 0, "documents_limits": None})
            report.append(row)
            continue

        source = suite.read_text(encoding="utf-8", errors="ignore")
        negatives = count_negative_assertions(source)
        row["suite"] = f"tests/{suite.name}"
        row["negative_assertions"] = negatives

        if negatives < min_negative:
            failures.append(
                f"tests/{suite.name} has {negatives} negative assertion(s), "
                f"minimum is {min_negative} — it does not demonstrate that "
                f"core/{module.name} refuses anything"
            )

        is_gate = bool(GATE_NAME.search(stem))
        row["is_gate"] = is_gate
        if is_gate:
            module_source = module.read_text(encoding="utf-8", errors="ignore")
            documented = bool(NON_COVERAGE_MARKER.search(module_source))
            row["documents_limits"] = documented
            if not documented:
                failures.append(
                    f"core/{module.name} is a gate but does not state its own "
                    "non-coverage — downstream restatements will overstate it"
                )
        else:
            row["documents_limits"] = None

        report.append(row)

    return failures, report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert every core mechanism proves what it refuses."
    )
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parent.parent,
        help="repository root (defaults to the repo containing this script)",
    )
    parser.add_argument("--min-negative", type=int, default=DEFAULT_MIN_NEGATIVE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    failures, report = audit(args.repo, args.min_negative)

    if args.json:
        import json

        print(json.dumps({"failures": failures, "modules": report}, indent=2, ensure_ascii=False))
        return 1 if failures else 0

    print("== Negative Test Coverage ==")
    for row in report:
        suite = row["suite"] or "MISSING"
        limits = row.get("documents_limits")
        limits_note = "" if limits is None else ("  limits:documented" if limits else "  limits:MISSING")
        print(f"  {row['module']:<34} {str(suite):<38} negative={row['negative_assertions']}{limits_note}")

    if failures:
        print("\nFAIL_LOUD [NEGATIVE_TEST_COVERAGE]:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nA suite that only exercises the accepted path goes green whether or\n"
            "not the refusal works. Add a case that asserts the refusal happens.",
            file=sys.stderr,
        )
        return 1

    print(f"\nRESULT: PASS ({len(report)} core module(s), each proving its refusals)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
