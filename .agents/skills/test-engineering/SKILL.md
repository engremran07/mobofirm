# Test Engineering

## Goal

Produce deterministic evidence that changed behavior meets its acceptance criteria.

## Steps

1. Reproduce the defect or add a focused failing regression test.
2. Place tests under the root `tests/backend/<app>/` or `tests/backend/integrations/`; use `tests/frontend/` only after a real frontend exists.
3. Cover the successful path and meaningful validation, authorization, rollback/compensation, and failure cases.
4. For cross-app behavior, assert the consumer-visible outcome and persisted state; cover duplicate delivery and transaction boundaries where relevant.
5. Use configured pytest/Django test settings and the isolated database. Verify pytest collected the intended tests.
6. Run focused tests, then the full suite and checks required by the quality gates.
7. Report test names, exact commands, outcomes, skips, and environment limitations.

## Constraints

- No network, real credentials, production data, sleeps, broad catches, or weakened assertions for ordinary tests.
- No migration execution against a non-test database without approval.
- Do not claim complete feature coverage from a green suite alone.
- Do not add tests that merely assert that a stub ran; test required observable behavior.
- Do not skip tests or weaken a regression assertion to get green. If the initial fix fails, revise the root-cause hypothesis instead of applying repeated symptom patches.
- Any retry loop must have bounded attempts, a justified transient condition, observable exhaustion, and deterministic tests. Any rollback/compensation must have defined trigger and persisted-state assertions.
