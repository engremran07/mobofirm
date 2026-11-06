# Mobofirm AI and Engineering Instructions

## Repository baseline

- This is a runnable Django project. `app/` contains project configuration and `apps/` contains the installed Django applications.
- The installed local applications are `users`, `core`, `ads`, `devices`, `blog`, `comments`, `firmwares`, and `tags` (plus the integrated `storage`, `seo`, `pages`, `shop`, `marketplace`, and `filemanager`). Verify `app/settings/base.py` before changing that inventory.
- Profile, following, and activity functionality belongs to `users`.
- Governance-pack policies, registers, and decisions are documentation under `docs/00-governance/`. The former `apps.governance` Django app was removed by owner decision: moderation is app-owned (firmware pending/approved/rejected with reviewer, timestamp and reason in `firmwares`; blog post status workflow in `blog`; comment moderation status in `comments`; tag curation in `tags`), operated by staff holding the relevant model permission.
- PostgreSQL is the configured runtime database. Tests use in-memory SQLite. Do not run migrations or cleanup commands against a non-test database without explicit authorization.
- The product owner has approved the frontend foundation: Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide, and Vite. Public and Django admin experiences use distinct shells: the public shell shares one token/static-asset source and the admin is django-unfold themed from the same brand tokens (new admin code extends `apps.core.admin_base`, never `django.contrib.admin.ModelAdmin` directly). The first-pass visual direction is light-first editorial with a balanced, multi-tone dark theme; final visual calibration remains a product/design review item, not a blocker to the approved functional implementation.
- Do not add dependencies or documentation files unless a requirement needs them. Preserve the existing `app/` and `apps/` separation.

## Zero-drift engineering mandate

Evolve the repository from verified source and tests, not historical prompts or assumptions. Every installed domain app must have a documented owner, explicit optional integration seams, and a standalone installation contract. Do not introduce hidden cross-app imports, duplicate models/services, or competing sources of truth. Existing cross-app dependencies are migration work, not permission to add more.

Do not ship placeholder feature implementations, success-shaped fallbacks, silently skipped tests, or unverified integrations. Every enabled user-facing or operational feature must have a traceable acceptance criterion, functional implementation, focused tests, and applicable integration/runtime verification. Unimplemented scope must be explicitly disabled or excluded rather than represented as complete.

For regressions, reproduce and trace the root cause before fixing it. Avoid repeated symptom patches and blind retry/reversal loops. If a fix fails, stop and revise the causal hypothesis; add a bounded rollback or compensation only when its trigger, state effects, and recovery are defined and tested.

All automated cross-app work must specify its owner, transaction/commit boundary, idempotency key, retry and terminal-failure behavior, and audit outcome. Firmware-to-blog automation owns exactly one post per brand, model and chipset (`firmwares.AutomatedBlogLink`), authored by the dedicated non-login automation user and refreshed after commit; it only lists firmware that staff have approved (`is_verified`).

## Test layout

- All project tests live at the repository root under `tests/backend/` or `tests/frontend/`; do not add app-local test directories.
- Backend tests are grouped by owning app under `tests/backend/<app>/`; cross-app contract tests belong under `tests/backend/integrations/`.
- Frontend-specific automated tests live under `tests/frontend/`; Django-rendered page and security contract tests live under `tests/backend/`. Playwright browser checks are required for frontend behavior, template, or layout changes.
- No applicable test may be silently skipped to obtain a green run. Every requirement in scope maps to tests and verification evidence.
- Run every Python/Django command with the repository virtual environment: `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux/macOS. Do not validate with a global interpreter.
- The canonical test command is `.venv\Scripts\python.exe -m pytest -q` on Windows (`.venv/bin/python -m pytest -q` on Linux/macOS); collection is configured in `pyproject.toml`.

## Stack-specific quality rules

- Python/Django: preserve app ownership, type public service boundaries, validate inputs, use atomic transactions for related writes, keep migrations forward-compatible, and test authorization, rejection, retries, and rollback semantics where applicable.
- Django templates, HTML, CSS, and JavaScript: preserve strict CSP, semantic/accessibility behavior, centralized tokens, responsive layouts, and server-side authorization. Do not add a competing frontend stack.
- Forms/authentication: use the global Django CSRF middleware, include CSRF tokens in HTML POST forms, gate sessions and account side effects on verified email, and redirect after successful browser POSTs. Use app-owned accessible UI instead of browser-native confirmation dialogs.
- Frontend changes require Playwright coverage for relevant routes, runtime/console failures, responsive overflow, and key interactions, in addition to the Vite build and focused Django tests.
- Do not add TypeScript or other language toolchains unless the product requires them; apply rules only to languages actually present in the repository.

## Required workflow

1. Read the relevant app instructions under `.github/instructions/` and the corresponding workflow skill under `.github/skills/`.
   Also read applicable policies under `docs/00-governance/`.
2. Trace the real callers, owning model/service, permissions, side effects, and integration boundary before changing behavior.
3. Add focused regression and contract tests in the root `tests/` tree for changed behavior. Cover success, rejection/failure, duplicate delivery, and rollback/compensation where applicable.
4. Run focused tests, the complete test suite for material behavior changes, Django checks, migration consistency, and dependency checks as applicable. Report exact commands and results.
5. Inspect the VS Code Problems panel after edits. Do not commit or push while it contains unresolved issues. Do not claim a clean Problems panel unless it was checked.
6. Preserve user changes. Never revert unrelated work, run destructive data operations, or commit/push without explicit authorization and a clear Problems panel.

## Canonical governance documentation

- Product and design approval state: `docs/01-product/`.
- App ownership and system structure: `docs/03-architecture/architecture-overview.md` and `docs/04-backend/django-app-boundaries.md`.
- Quality, security, and release gates: `docs/00-governance/policies/` and `docs/00-governance/definition-of-done.md`.
- Agent boundaries and approval responsibilities: `.agents/policies/`.
- Repository discovery and testing workflow: `docs/engineering-workflow.md`.

The source-of-truth ordering is defined in `.agents/policies/source-of-truth.yaml`. Governance is documentation, not a substitute Django app. If code and documentation disagree, report the discrepancy and treat running code, settings, and migrations as implemented-state evidence; do not silently invent product approval.
