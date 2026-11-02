---
description: "Use when changing Python or Django code in blog, comments, firmwares, or tags. Covers app boundaries, data integrity, migrations, and regression coverage."
applyTo: "**/*.py"
---
# Django and Python

- Inspect the full caller/callee path and existing tests before changing behavior. Keep public APIs, app ownership, and current import paths compatible.
- Put business behavior in the existing service/model/task layer; keep views and signals thin. Make signal-triggered work idempotent and avoid hidden repeated side effects.
- Validate inputs at boundaries, use ORM parameters, and use transactions for related writes. Use `transaction.on_commit()` when side effects must follow a committed write.
- Test the regression and relevant permissions, invalid input, failure, and duplicate/retry paths. Use Django `TestCase` and existing fixtures/patterns where available.
- Review generated migrations for accidental data loss, lock-heavy operations, and missing reversibility. Run migration checks only when a real project settings module is available.
- Do not add a dependency, settings module, or project scaffold to compensate for files missing from this checkout.
