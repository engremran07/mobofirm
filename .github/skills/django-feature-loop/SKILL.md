---
name: django-feature-loop
description: "Closed-loop Django feature implementation and bug fixing across blog, comments, firmwares, and tags: trace behavior, reproduce, regression-test, fix, validate, and report."
---
# Django Feature Loop

Use for a Django/Python feature, bug, refactor, migration, or regression in one of the connected apps.

1. Confirm the request, owning app, public behavior, and relevant callers. Read the model/service/view/task/signal paths and nearby tests; distinguish existing facts from missing project infrastructure.
2. Reproduce the issue or write a failing focused regression test when practical. Record the expected observable behavior, including authorization and failure semantics.
3. Implement the smallest complete fix in the owning layer. Keep a single source of truth; preserve import/API compatibility, transaction boundaries, and migration reversibility.
4. Add/update tests: regression; success and meaningful invalid/failure cases; app contract/integration and duplicate/retry cases when connected behavior changes.
5. Run the focused tests, then applicable app/project checks, migration checks, and configured quality tools. Do not claim Django checks can run without settings or that the app suite passed when the Django project shell is absent.
6. If a test fails, inspect the entire error and trace it to the producer. Do not add retries, broad exception handlers, skip markers, weakened assertions, or “band-aid” loops without a demonstrated transient failure and bounded policy.
7. Report exact commands/results, what could not run, and any migration, data, security, or observability risks.

For harder-to-reproduce failures, continue with `deep-verification`. For behavior spanning apps, use `inter-app-automation`.
