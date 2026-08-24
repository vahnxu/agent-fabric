# Agent Operations Fabric — documentation index

## What lives where

| Path | Contents |
|------|----------|
| `docs/01_master_thesis.md` | Why institutional cost collapses when the executor is an agent |
| `docs/02_minimal_kernel.md` | The four fundamental laws, the compounding loop, three files to start |
| `docs/03_governance_architecture.md` | Layered contracts (L1/L2/L3), runtime decoupling, rotation topology |
| `docs/04_incident_casebook.md` | Production incidents, each inverted into the invariant it taught |
| `docs/05_curriculum.md` | A seven-session syllabus built on the casebook |
| `docs/workspace/logs/` | Execution logs, one per session, `YYYYMMDD_<Summary>.md` |

## How the repository is organised

| Path | Contents |
|------|----------|
| `core/` | The mechanisms themselves. Each one refuses something, and says in its own docstring what it does **not** cover. |
| `tests/` | One suite per mechanism. Negative cases lead: a suite that only walks the accepted path goes green whether or not the refusal works. |
| `ops/` | Repository gates, run identically on a laptop and in CI. |

## Onboarding gate

Every agent runs this before touching anything, every session:

```bash
./ops/enforce_agent_onboarding_gate.sh
```

It composes three checks — governance consistency, negative test coverage, and
the full suite — and refuses on the first failure. Having passed before does not
count.

## Reading order for a new contributor

1. `docs/01_master_thesis.md` — the premise: text is a liability, mechanism is an asset.
2. `docs/02_minimal_kernel.md` — the smallest thing you can adopt today.
3. `core/pre_tool_use_safety.sh` — the shortest mechanism, and a worked example of
   asserting an invariant instead of enumerating a past incident.
4. `tests/test_pre_tool_use_safety.py` — what the same mechanism must refuse, and
   what it must not falsely block.
