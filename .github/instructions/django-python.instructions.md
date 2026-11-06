---
description: "Use when changing Python or Django code in any installed Mobofirm application. Covers app boundaries, data integrity, migrations, and regression coverage."
applyTo: "**/*.py"
---
# Django and Python

- Inspect the full caller/callee path and existing tests before changing behavior. Keep public APIs, app ownership, and current import paths compatible.
- Put business behavior in the existing service/model/task layer; keep views and signals thin. Make signal-triggered work idempotent and avoid hidden repeated side effects.
- Validate inputs at boundaries, use ORM parameters, and use transactions for related writes. Use `transaction.on_commit()` when side effects must follow a committed write.
- Test the regression and relevant permissions, invalid input, failure, and duplicate/retry paths. Use Django `TestCase` and existing fixtures/patterns where available.
- Review generated migrations for accidental data loss, lock-heavy operations, and missing reversibility. Run migration checks only when a real project settings module is available.
- Use Ruff as the canonical Python formatter. Check formatting with `.venv\Scripts\ruff.exe format --check .` on Windows or `.venv/bin/ruff format --check .` on Linux/macOS.
- Run all Python/Django commands through the repository virtual environment (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/macOS); do not fall back to a global interpreter.
- Project configuration belongs in `app/`; domain applications belong in `apps/`. Verify installed apps in `app/settings/base.py`.
- Keep profile, following, and activity behavior in `apps.users`; do not recreate a separate profile app.
- Keep `CsrfViewMiddleware` as the global CSRF authority. Do not exempt browser/session-authenticated writes; check verified email before login side effects or protected workflows; redirect after successful browser POSTs.
- For Django REST Framework, preserve `SessionAuthentication` CSRF checks and keep token-authenticated API behavior explicit. Test unsafe requests with CSRF enforcement enabled.
- In the firmware catalog, keep firmware classification/provenance separate from package format and file extension. Preserve manufacturer model codes at the variant level; do not create downloadable firmware records without a real staged file (or an allowlisted external HTTPS URL) and staff approval through the firmware moderation services.
- Catalog seed commands must be deterministic, additive, local-target guarded, and safe to rerun. Never seed placeholder firmware binaries or directly publish generated editorial content.
- Do not add references to apps that are not installed, dependencies, frontend assets, or settings modules without explicit approval.
- Do not apply migrations or cleanup commands to a non-test database without explicit authorization.
- Implement the requested behavior end to end; do not silently substitute a static preview, partial format list, or unrelated feature. If a capability is unsupported or unsafe, trace the concrete constraint, state the consequence, and ask before narrowing the requested behavior.
- Do not add broad `except Exception` or `except BaseException` handlers, silent fallbacks, or lint suppressions. Catch expected concrete exception types, preserve failure semantics, and log or surface actionable context.
- For a zero-diagnostics request, inventory the whole first-party repository, including existing findings; fix findings rather than disabling diagnostics. Exclude nested/reference repositories unless the user explicitly includes them, and report any remaining blocker without claiming a clean workspace.
