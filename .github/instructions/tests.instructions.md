---
description: "Use when creating or changing Python tests, migrations, browser checks, or app integration tests."
applyTo: ["**/tests.py", "**/test_*.py", "**/*_test.py", "**/migrations/*.py"]
---
# Tests and Migrations

- Make tests deterministic: isolate external services, freeze/control time where needed, and avoid network, real credentials, production data, or sleep-based synchronization.
- Assert observable outcomes and authorization boundaries, not private implementation details. For each changed feature, cover a success case and its important failure or regression case.
- For connected apps, exercise the public integration boundary and retry/duplicate behavior; test database constraints and transaction semantics where they matter.
- Keep migration tests separate from routine unit tests when a test would require special database setup. Review generated SQL and reversibility for risky schema changes.
- Run Python tests with the repository `.venv` interpreter (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/macOS).
- Frontend behavior, template, or layout changes require Playwright route, responsive-overflow, browser-error, and key-interaction coverage. Do not silently skip missing browser setup.
- Firmware browser checks should exercise both firmware classification and package-format filters, verified/unclassified routing, official-tool links, and representative narrow and desktop widths; capture console errors and warnings without suppressing unrelated diagnostics.
- Add Android emulator coverage only when a real Android application and SDK/emulator exist. Clearly state unavailable validation rather than substituting a weaker check without disclosure.
