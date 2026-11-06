---
name: django-feature-loop
description: "Closed-loop Django feature implementation and bug fixing across blog, comments, firmwares, and tags: trace behavior, reproduce, regression-test, fix, validate, and report."
---
# Django Feature Loop

Use for a Django/Python feature, bug, refactor, migration, or regression in one of the connected apps.

1. Translate the user's request into observable acceptance criteria before implementation. Confirm the owning app, public behavior, and relevant callers; read the model/service/view/task/signal paths and nearby tests. Do not silently narrow the scope or replace a requested functional flow with a static mock. Distinguish existing facts from missing project infrastructure.
2. Reproduce the issue or write a failing focused regression test when practical. Record the expected observable behavior, including authorization and failure semantics.
3. Implement the smallest complete fix in the owning layer. Keep a single source of truth; preserve import/API compatibility, transaction boundaries, and migration reversibility.
4. Add/update tests: regression; success and meaningful invalid/failure cases; app contract/integration and duplicate/retry cases when connected behavior changes.
5. Run Ruff formatting checks, then focused tests, applicable app/project checks, migration checks, and configured quality tools through the repository `.venv` (`.venv\Scripts\python.exe` and `.venv\Scripts\ruff.exe` on Windows; `.venv/bin/python` and `.venv/bin/ruff` on Linux/macOS). Do not claim Django checks can run without settings or that the app suite passed when the Django project shell is absent.
6. If a test or diagnostic fails, inspect the full relevant error and trace it to the producer. Never add broad exception handlers, silent fallbacks, diagnostic suppressions, skip markers, weakened assertions, or “band-aid” loops to obtain a green result. Catch only concrete expected exceptions and preserve meaningful failure behavior.
7. For requested seed runs, verify the active database vendor/target first, require explicit local-development authorization, run only the guarded idempotent command, and report the exact records and publication state changed. Reconcile pre-existing seeded records when create-only signals cannot catch them up.
8. Report exact commands/results, what could not run, and any migration, data, security, or observability risks. For zero-diagnostics requests, verify the full first-party workspace in Problems and the applicable lint/type checks; do not claim success while any remain.

For browser forms and authentication, also preserve global CSRF middleware and `{% csrf_token %}` form fields, test rejection with CSRF enforcement enabled, require verified email before session/device/workflow side effects, and redirect after successful POSTs. Never implement product confirmations with browser-native dialogs.

For harder-to-reproduce failures, continue with `deep-verification`. For behavior spanning apps, use `inter-app-automation`.
