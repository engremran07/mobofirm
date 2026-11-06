---
name: zero-diagnostics
description: "Use for requests to clear the VS Code Problems panel, achieve zero errors or warnings, or fix repository-wide Ruff, Pylance, type-check, or exception-handling findings without suppressions."
---
# Zero Diagnostics Workflow

## Procedure

1. Confirm the workspace root and the intended repository scope. Treat nested reference repositories such as `GSMFWs/` as out of scope unless explicitly included.
2. Capture the full Problems panel and the repository's configured lint/type-check baselines, including strict Pylint with the project's Django settings. Separate first-party defects from missing third-party types, stale interpreter selection, generated files, and unrelated reference projects.
3. Group findings by root cause and owning app. Do not fix by silencing rules, lowering severity, adding `noqa` or type-ignore comments, or skipping tests.
4. For exception-handling findings, trace every caught operation and identify its concrete expected exception classes. Preserve rollback, authorization, privacy, and failure semantics; never substitute `BaseException` or another catch-all.
5. Fix one bounded group at a time and add regression coverage for behavior changes. Run focused tests, then the whole applicable test suite and configured checks.
6. Re-run the complete Problems and lint/type inventory. Continue until zero remains in scope, or stop and ask about a documented external/tooling blocker.
7. Report exact commands and counts. Claim a clean panel only when the final Problems check shows zero diagnostics.
