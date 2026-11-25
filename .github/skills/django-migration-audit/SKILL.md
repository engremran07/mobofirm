---
name: django-migration-audit
description: Inspect and safety-audit Django model and migration changes in Mobofirm. Use when changing schema, constraints, indexes, or migration files.
argument-hint: "[app_name]"
---
# Django Migration Audit

1. Read the owning app's models, migration history, app instructions, and relevant
   migration tests before changing schema.
2. Use `.venv\Scripts\python.exe` on Windows (or `.venv/bin/python` elsewhere).
   Inspect proposed migrations with `manage.py makemigrations <app> --dry-run
   --verbosity 2`; do not apply migrations during this audit.
3. Check operation ordering, data-loss risk, reversibility, defaults/nullability,
   indexes, lock impact, and constraint names. Never rewrite a migration already
   shared with users; add a new migration instead.
4. Run `manage.py makemigrations --check --dry-run` and
   `manage.py check` only with isolated test settings. Never target the configured
   PostgreSQL database without explicit authorization.
5. Report each operation, its risk, and the exact checks run. A generated
   migration is not safe merely because Django produced it.
