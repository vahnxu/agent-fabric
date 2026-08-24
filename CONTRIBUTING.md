# Contributing to Agent Operations Fabric

Thank you for your interest in contributing to **Agent Operations Fabric**!

Agent Operations Fabric is built on a fundamental principle: **"Every production failure must be compiled into a deterministic mechanism on the same day."**

We welcome community contributions, incident case studies, reference implementations, and governance tools that adhere to our core philosophy.

---

## Core Contribution Philosophy

1. **Deterministic Boundaries Over Prompt Engineering**:
   - We do not accept PRs that merely "add advice or soft warnings" into system prompts.
   - If an Agent makes a mistake, the solution must be a **hard gate, deterministic validator, or state machine**.
2. **Incident-Driven & Evidence-Based**:
   - Every new rule or tool must reference a concrete problem statement, failure mode, and verification test.
3. **Multi-Runtime Decoupling**:
   - Core governance logic must remain vendor-agnostic (Claude Code, OpenAI Codex, Google Antigravity, Gemini CLI, etc.).
   - Model-specific adapters belong in explicit adapter layers, not in core.

---

## Development & Testing Workflow

### 1. Prerequisites
- Python 3.10+
- Bash / POSIX Shell environment
- Git

### 2. Running Local Verification Suite
Before opening a Pull Request, all unit tests and governance onboarding gates must pass:

```bash
# Run all unit tests
python3 -m unittest discover tests/

# Run governance consistency check
./ops/check_release_governance_consistency.sh

# Run agent onboarding gate
./ops/enforce_agent_onboarding_gate.sh
```

---

## Directory Conventions

- `docs/` — Architecture whitepaper, governance specifications, and incident casebook.
- `core/` — Reference implementations for task ledgers, supervisors, safety hooks, and gates.
- `tests/` — Comprehensive unit test suites corresponding to `core/` modules.
- `ops/` — Verification scripts and continuous integration gates.

---

## Pull Request Checklist

- [ ] Unit tests added in `tests/` for all new core logic.
- [ ] `./ops/enforce_agent_onboarding_gate.sh` passes locally.
- [ ] No hardcoded secrets, internal credentials, or private machine hostnames.
- [ ] Markdown documentation is clear, concise, and structured.
