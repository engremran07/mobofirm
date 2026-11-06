# Governance Pack

This repository's governance pack is the combined control system that keeps
implementation, evidence, and review in one closed loop.

## The pack is not only documentation

The governance pack includes all of the following layers:

1. Policy and authority
   - `AGENTS.md`
   - `docs/00-governance/registry.yaml`
   - `docs/00-governance/policies/*.yaml`
   - `docs/00-governance/definition-of-done.md`

2. Agent decision rules
   - `.github/instructions/*.instructions.md`
   - the relevant `.github/skills/*/SKILL.md` workflows
   - `.github/agents/*.agent.md` specialized roles

3. Enforcement and proof
   - `scripts/application_map.py`
   - `scripts/feature_impact.py`
   - `scripts/governance_audit.py`
   - `scripts/governance_pack.py`
   - tests under `tests/backend/core/`

4. Quality gating and release checks
   - `docs/00-governance/policies/quality-gates.yaml`
   - `docs/00-governance/definition-of-done.md`
   - `.github/workflows/ci.yml`

## Closed-loop operating model

The pack works as a single loop:

- policy defines required controls and approval state
- instructions constrain behavior before and during implementation
- skills provide repeatable execution patterns
- agents own bounded responsibilities and evidence
- scripts validate the repository state and impact traceability
- tests provide the final proof of behavior
- release and verification gates reject unverified changes

## Required invariant

No implementation is considered complete until the repository can show all of the
following:

- owning app identified
- affected routes, templates, assets, and callers traced
- relevant regression tests exist
- no skipped/xfail evidence in relevant test paths
- pass/fail evidence recorded from runnable checks
- no unresolved Problems-panel issues for the changed files

This document is the human-readable summary of the system; the executable checks
in `scripts/governance_pack.py` and `scripts/governance_audit.py` are the proof
that the pack is actually present and enforced.
