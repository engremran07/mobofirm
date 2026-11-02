# Engineering Workflow

This checkout currently contains four interrelated Django app packages: `blog`, `comments`, `firmwares`, and `tags`. It does not contain a Django project entry point/settings, dependency manifest, frontend build manifest, or Flutter/Android project. Re-check the repository before relying on that inventory; validation commands below are conditional on a complete project being present.

## Workspace workflows

The repo-local skills are available as Chat slash commands:

- `/django-feature-loop` — implement a feature or regression end to end.
- `/inter-app-automation` — change or debug workflows crossing app boundaries.
- `/frontend-stack` — inspect and verify configured frontend work.
- `/observability-retention` — handle structured logs, telemetry, and safe retention.
- `/deep-verification` — escalate verification and diagnose hard-to-reproduce failures.

Use the **Django Feature Engineer** agent for bounded implementation and the **Verification Engineer** agent for read-only independent validation. A session-start hook inventories the actual project files; it does not install dependencies, mutate code, or run tests.

## Feature-to-evidence loop

For each feature or defect, record its user-observable behavior, owning app, affected integration boundaries, and a focused regression test. Add contract/integration coverage when an event, task, service, model, or API crosses app ownership. Include permission, invalid input, duplicate/retry, and failure cases where applicable. A test must assert the required outcome, not merely exercise the code.

Implement at the existing owning layer, keep state authoritative in one place, and preserve transaction semantics. Review generated migrations for reversibility and data/locking impact. Run the focused test first, then the broader relevant suite and configured static/runtime checks. Do not report unavailable commands as passing.

## Validation ladder

Choose the strongest applicable, configured and runnable evidence:

1. Focused unit/regression tests and app contract/integration tests.
2. Django checks, migration consistency, and database-backed integration using project settings and the intended database.
3. Configured formatting, lint, type, dependency/security, and migration SQL review.
4. HTTP/browser end-to-end and accessibility checks when templates/frontend assets and a browser runner exist.
5. Android emulator/device permission, lifecycle, network, and telemetry tests only when a real Android client, SDK, and emulator are present.
6. For persistent or intermittent defects: minimized reproduction, debugger/runtime trace, structured correlated logs, browser network/console inspection, controlled concurrency, or database query plan. Add property/mutation/fuzz tests only where they exercise a concrete risk.

Always distinguish passed, failed, skipped, and unavailable checks and include the actual command/evidence. Read complete errors before fixing. Never hide a defect with a broad exception, blind retry, skip, weakened assertion, or repeated “band-aid” patch. Fix the root cause, add regression coverage, and re-run the original failure.

## Connected-app boundaries

Treat each app's models and services as owned state. Trace real callers before changing APIs. Prefer explicit service contracts over implicit signal chains. Make asynchronous handlers idempotent; dispatch side effects after transaction commit. Use bounded, observable retries only for demonstrated transient failures. Test rollback, duplicate delivery, retry exhaustion, permissions, and final persisted outcomes when relevant.

## Frontend, logs, and privacy

Use only frontend libraries/build systems actually present or explicitly approved. Django remains the server-side authorization and data authority. Verify accessibility, CSRF, escaping, focus, responsive/error states, and browser behavior when runnable.

Log structured outcomes and correlation IDs, not credentials, tokens, full request bodies, or unnecessary personal/device data. Define a configurable age/size retention policy for the designated log sink; cleanup must be bounded, scoped, testable, and safe around active writes. Do not purge global environments, arbitrary folders, or logs without explicit scope and authorization. Mobile collection additionally requires a real client and defined consent, permissions, transport, and retention.

## Local commands

The project-local environment is `.venv`. Once a complete Django project and dependencies are available, use its configured commands; typical Django commands require a valid settings module:

- Windows: `.venv\\Scripts\\python.exe manage.py check`
- Windows: `.venv\\Scripts\\python.exe manage.py test`
- Windows: `.venv\\Scripts\\python.exe manage.py makemigrations --check --dry-run`

These commands cannot run successfully from an app-only checkout without `manage.py`, settings, required applications, and dependencies. Do not create substitute project configuration merely to make a check appear green.
