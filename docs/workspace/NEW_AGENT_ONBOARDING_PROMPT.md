# New agent onboarding — Agent Operations Fabric

## Before anything else

```bash
./ops/enforce_agent_onboarding_gate.sh
```

This runs the governance consistency check, the negative test coverage gate, and
the full test suite. Do not begin work until it prints
`[PASS] AGENT_ONBOARDING_GATE_PASSED`. A previous session's pass is not evidence
about this one.

## What this repository is

The reference implementation and specification of a governance control plane for
agent fleets. Its claim is narrow and testable: a rule that lives in a system
prompt is forgotten, drifts between model versions and costs context on every
turn, whereas a rule compiled into a hook, a gate or a state machine costs
nothing per turn and holds regardless of which model is running.

`core/` is where that claim is cashed out. Each module refuses something.

## The four rules that govern changes here

1. **A mechanism ships with its refusal test.** A new or changed `core/` module
   must carry a test asserting what it refuses. `ops/check_negative_test_coverage.py`
   enforces this — it is not a matter of judgement.
2. **A gate states its own non-coverage.** If your mechanism cannot see through
   variable indirection, or only inspects literal strings, say so in its
   docstring. Anyone restating the gate downstream inherits that sentence; without
   it they will overstate what is protected.
3. **Assert the invariant, not the incident.** A gate written against the exact
   syntax of last week's failure is bypassed the moment the same invariant breaks
   through a different carrier. `docs/02_minimal_kernel.md` § Meta-Rule 1.
4. **Report degradation out loud.** A skipped step, an unverified claim, a
   partial completion — each must be stated. A silent success is the most
   expensive lie a governance system can tell.

## Where work is recorded

Execution logs go in `docs/workspace/logs/YYYYMMDD_<Summary>.md`: what changed,
what was run and what it printed, what remains open, and where the rollback point
is.
