---
description: "Use when asked to clear the VS Code Problems panel, reach zero diagnostics, or remediate repository-wide lint/type errors and warnings."
---
# Zero Diagnostics

- Establish the Git root and identify nested or reference repositories; include only the user's requested scope.
- Capture baseline Problems and configured lint/type-check findings before editing. Group by root cause and file, not just message text.
- Run the configured strict Pylint command with the repository Django settings; include first-party app, project, script, and test code without reference repositories.
- Fix source defects and missing supported type information at the cause. Do not disable checks, lower severity, add `noqa`/type-ignore suppressions, or remove tests to manufacture zero findings.
- For broad exception lint findings, inspect each operation and catch its concrete expected failures. Do not replace `Exception` with another catch-all or silently continue.
- Work in bounded batches, run focused regression tests, then repeat the full first-party diagnostic inventory until it is genuinely clean.
- Verify the final Problems panel and exact configured quality commands. If environmental or third-party diagnostics cannot be fixed without a dependency or scope decision, explain the evidence and ask; never report zero.
