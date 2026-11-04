---
name: deep-verification
description: "Go beyond unit tests for difficult Django or web regressions using layered integration, browser/emulator checks, static analysis, mutation/property tests, runtime traces, and evidence-based diagnosis."
---
# Deep Verification

Use when a defect survives ordinary tests, a user asks for comprehensive feature verification, or a change affects app contracts, browser behavior, migrations, or telemetry.

1. Write down the observable invariant and a minimal reproducible input. Check existing tests, dependencies, runtime, configuration, and available browser/mobile/database services first.
2. Run Python/Django commands through the repository `.venv` interpreter. Verify in layers: focused unit/regression test; Django system/deployment and migration checks; database-backed app integration/contract tests; configured static/type/security checks; required Playwright browser end-to-end tests for frontend changes; Android emulator/device tests only if a real app and SDK/emulator exist.
3. Inspect the complete failure output, request/response, logs, database state, and call path. Use a debugger or trace for control-flow/state problems, browser console/network inspection for UI issues, and query plans for database performance. Preserve privacy and redact captured artifacts.
4. Escalate when coverage is insufficient: add a minimized regression test; use property-based tests for input invariants, mutation testing to assess assertion quality, or bounded fuzzing for parsers/validation if tooling and risk justify it. Compare controlled runtime traces and metrics for timing/race/production-only faults. Do not label these methods “more advanced” substitutes for a reproducible test.
5. For intermittent failures, establish a stable repro and isolate time, concurrency, external dependencies, and ordering. Change a retry/timeout only with evidence, finite limits, backoff, and terminal observability.
6. Repeat the original failing validation after the fix; then run the nearest broader suite. Report passed/failed/skipped/unavailable distinctly and attach no secrets, production data, or unredacted logs.
