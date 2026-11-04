# Testing Strategy

## Test layers

1. **Focused regression:** reproduce the changed behavior in the owning app.
2. **App contract/integration:** assert cross-app persisted and user-visible outcomes for changed service, signal, task, or governance boundaries.
3. **Project suite:** run every configured pytest test with the isolated Django test settings.
4. **Django checks:** system checks and migration consistency use `app.settings.test`.
5. **Dependency consistency:** run `pip check` after environment/dependency changes.
6. **Browser and accessibility validation:** Playwright coverage is required for frontend behavior, templates, or layout changes. The test suite checks named routes, browser errors, runtime initialization, responsive overflow, and key interactions. Production-database/provider validation remains separate and requires isolated infrastructure.

## Commands

```powershell
.venv\Scripts\python.exe -m pytest tests\backend\devices\test_devices.py -q
.venv\Scripts\python.exe -m pytest --collect-only -q
.venv\Scripts\python.exe -m pytest -q
$env:DJANGO_SETTINGS_MODULE = "app.settings.test"
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe -m pip check
.venv\Scripts\python.exe -m playwright install chromium
```

On Linux/macOS, substitute `.venv/bin/python` for `.venv\Scripts\python.exe`. Create and activate the repository's `.venv` before installing or running project Python commands. Never use a global interpreter for project validation.

Tests use in-memory SQLite, a fast password hasher, and eager Celery execution. They do not prove PostgreSQL behavior, Redis delivery, external provider behavior, or production performance.

## Evidence

Record the actual focused and full-suite outcomes for each change. Historical passing counts are not current evidence. Template-reference checks ensure literal template names used by installed apps resolve; they do not prove that every route or feature behaves correctly.

## Test quality

- Assert the required observable result, not only that a function ran.
- Cover permissions, invalid input, failure behavior, and duplicate delivery where applicable.
- Avoid real secrets, production data, network calls, and timing-dependent sleeps.
- Do not hide failures with skip markers, broad exception handling, or success-shaped defaults.
