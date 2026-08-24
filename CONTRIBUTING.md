# Contributing to Agent Operations Fabric

Agent Operations Fabric rests on one claim: **a rule that lives in prose is a
liability, and a rule compiled into a mechanism is an asset.** Contributions are
judged against that claim, so the bar here is unusual in one specific way — we
care more about what your change *refuses* than about what it enables.

---

## Core contribution philosophy

1. **Deterministic boundaries over prompt engineering.**
   A pull request that adds advice, a warning, or a "please remember to…" line to
   a system prompt will not be merged. If an agent gets something wrong, the fix
   is a hard gate, a deterministic validator, or a state machine.

2. **Assert the invariant, not the incident.**
   A gate written against the exact syntax of last week's failure is bypassed the
   moment the same invariant breaks through a different carrier. State the
   invariant, then detect violations of *it*. See `docs/02_minimal_kernel.md`
   § 元规则一 and the worked example in `core/pre_tool_use_safety.sh`.

3. **A mechanism ships with its refusal test.**
   A suite that only exercises the accepted path goes green whether or not the
   refusal works — this repository shipped four such modules and every one of
   them was broken. Your `core/` change must carry:
   - **negative cases** — the gate must block these;
   - **false-positive cases** — the gate must *not* block these. A gate that
     cries wolf gets switched off, which is worse than no gate.

   `ops/check_negative_test_coverage.py` enforces the first half mechanically.
   The second half is on you and on review.

4. **A gate states its own non-coverage.**
   If your mechanism only sees literal strings, cannot follow indirection, or
   stops at the repository boundary, write that in its docstring under
   `KNOWN NON-COVERAGE`. Anyone restating your gate downstream inherits that
   sentence. Without it they will overstate what is protected, and a gate
   believed to cover more than it does is more dangerous than none.

5. **Runtime decoupling.**
   Core logic must not name a vendor, a product, or a model — not in code, not in
   identifiers, not in test fixtures. Sessions are matched by *capability*.
   Runtime-specific details belong in an explicit adapter supplied by the caller.

6. **No private data, ever.**
   No absolute home directories, no machine hostnames, no personal identifiers,
   no credential material — in any tracked file, gate scripts and fixtures
   included. `ops/check_release_governance_consistency.sh` scans for this and
   will refuse the commit. Fixtures needing a home-shaped path must use the
   reserved examples `/Users/test`, `/Users/example`, `/home/user`, `/home/runner`.

---

## Development and verification

### Prerequisites
- Python 3.10 or newer (CI covers 3.10 – 3.13)
- Bash / POSIX shell
- Git

### The one command
```bash
./ops/enforce_agent_onboarding_gate.sh
```
It composes all three gates and refuses on the first failure. Run it before you
open a pull request; run it again before you push.

### The three gates individually
```bash
./ops/check_release_governance_consistency.sh   # instructions agree · no private data · target sane
./ops/check_negative_test_coverage.py           # every mechanism proves what it refuses
python3 -m unittest discover tests/             # the mechanisms actually behave that way
```

### If you edit the agent instructions
`AGENTS.md` is canonical; `CLAUDE.md` and `GEMINI.md` are kept identical so no
runtime reads a different rulebook. Edit `AGENTS.md`, then:
```bash
./ops/sync_agent_instructions.sh
```

---

## Directory conventions

| Path | Contents |
|---|---|
| `docs/` | Specification, architecture, incident casebook, curriculum |
| `core/` | Reference mechanisms. Each refuses something and documents its limits. |
| `tests/` | One suite per mechanism, negative cases first |
| `ops/` | Repository gates, identical locally and in CI |

---

## Pull request checklist

- [ ] `./ops/enforce_agent_onboarding_gate.sh` passes locally.
- [ ] New or changed `core/` logic has **negative** tests asserting the refusal.
- [ ] New or changed gates also have **false-positive** tests asserting what they let through.
- [ ] Any new gate documents `KNOWN NON-COVERAGE` in its own docstring.
- [ ] No vendor, product or model name in `core/`.
- [ ] No absolute home paths, hostnames, personal identifiers or credentials anywhere.
- [ ] If the change came from a real failure, `docs/04_incident_casebook.md` gains
      the case in the standard four-part form: what happened / the invariant /
      the mechanism it compiled into / how you verify the mechanism is alive.
