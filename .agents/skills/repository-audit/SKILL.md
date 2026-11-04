# Repository Audit

## Goal

Audit the current implementation and its evidence without assuming the repository is complete or clean.

## Steps

1. Read the complete relevant caller/callee path, app ownership, settings, migrations, and nearby tests.
2. Generate `application_map.json` with `scripts/application_map.py`; use it as an evidence index for installed apps, models, URL callbacks, template interactions, frontend assets, and test files.
3. Treat map edges as static candidates only. Verify route behavior and HTMX/Alpine lifecycle in the running browser; the map cannot establish product intent, permissions, or runtime correctness.
4. Check imports and integrations against the apps actually installed in `app/settings/base.py`.
5. Check every domain app's standalone import/install contract. Trace sibling-app references and distinguish app-owned integration adapters from leakage.
6. Enumerate root `tests/backend/` and `tests/frontend/`; verify all discovered test modules are collected and no app-local test tree remains.
7. Run focused tests first, then the full suite, Django checks, migration consistency, and dependency consistency as applicable.
8. Inspect permissions, data ownership, side effects, transaction boundaries, idempotency, compensation, and error handling at changed boundaries.
9. Search active code for stub implementations, silent fallbacks, unreachable behavior, and skipped tests; only flag confirmed feature gaps.
10. Check documentation and frontend/build inventory against the source rather than copying legacy claims.
11. Separate confirmed defects, coverage gaps, environment limitations, and proposals.

## Exit criteria

The audit report states evidence and exact checks. A passing test suite is not proof that every feature has coverage; list untested surfaces as gaps. Never commit or push with unresolved Problems-panel issues.
