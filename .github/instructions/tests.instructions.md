---
description: "Use when creating or changing Python tests, migrations, browser checks, or app integration tests."
applyTo: ["**/tests.py", "**/test_*.py", "**/*_test.py", "**/migrations/*.py"]
---
# Tests and Migrations

- Make tests deterministic: isolate external services, freeze/control time where needed, and avoid network, real credentials, production data, or sleep-based synchronization.
- Assert observable outcomes and authorization boundaries, not private implementation details. For each changed feature, cover a success case and its important failure or regression case.
- For connected apps, exercise the public integration boundary and retry/duplicate behavior; test database constraints and transaction semantics where they matter.
- Keep migration tests separate from routine unit tests when a test would require special database setup. Review generated SQL and reversibility for risky schema changes.
- Add browser or Android emulator coverage only when the corresponding runnable application and toolchain exist. Clearly state unavailable validation rather than substituting a weaker check without disclosure.
