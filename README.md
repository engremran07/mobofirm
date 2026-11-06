# Mobofirm

Mobofirm is a Django project with a PostgreSQL-backed runtime and installed domain/support applications. The codebase includes user and profile management, device identity, firmware management (catalogue, chipsets, gated downloads, external download sources), advertising, blog and comments, tagging, and app-owned moderation workflows.

The user has approved an initial hybrid frontend foundation: Django templates with Bootstrap, HTMX, CSP-safe Alpine, Lucide, and Vite. Public and Django admin experiences have distinct shells: the public site uses the Vite-built token/static-asset source, and the admin is django-unfold themed from the same brand tokens. The implementation is functional but the final visual identity and detailed design calibration remain open for review.

## Repository layout

```text
app/                 Django settings, root URLs, health checks, ASGI/WSGI
apps/                Installed, independently-owned Django applications
docs/                Product, architecture, quality, security, and operations
docs/00-governance/   Documentation-only engineering governance
tests/               Project-wide backend and frontend verification
.agents/              Agent roles, policies, and workflow skills
.github/              Existing Copilot customizations and CI
manage.py
pyproject.toml
```

Keeping project configuration in `app/` and business apps as siblings in `apps/` is deliberate: it separates Django deployment/bootstrap concerns from domain packages without nesting business code inside the project package. The apps have separate ownership, but standalone installation is not yet verified across all apps; see the app-boundary document for the current goal and remaining coupling work.

## Requirements

- Python 3.12 or newer.
- PostgreSQL for local application use.
- Redis is needed only when running asynchronous Celery workers/tasks.
- Runtime and development Python dependencies are declared in `pyproject.toml`.

## Local setup (Windows PowerShell)

1. Create and activate a virtual environment:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

2. Install the project and its existing test tools:

   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]"
   ```

3. Copy `.env.example` to `.env`. Replace every placeholder, especially `DJANGO_SECRET_KEY` and the PostgreSQL credentials. Create the database/user separately; do not use production credentials for local development.

4. Local settings use the isolated PostgreSQL schema `mobofirm_local`, leaving `public` untouched. Create that schema as the configured database user, then apply migrations:

   ```powershell
   python manage.py shell -c "from django.db import connection; connection.cursor().execute('CREATE SCHEMA IF NOT EXISTS mobofirm_local AUTHORIZATION CURRENT_USER')"
   python manage.py migrate
   python manage.py check
   ```

5. Run the development server:

   ```powershell
   python manage.py runserver
   ```

The application settings default to `app.settings.local`; that settings module uses PostgreSQL and routes local development to `mobofirm_local`. The existing `public` schema is not migrated or rewritten. Do not run `migrate` until the database target has been confirmed. The test settings module uses an in-memory SQLite database and eager Celery execution.

## Verification

```powershell
python -m pytest -q
$env:DJANGO_SETTINGS_MODULE = "app.settings.test"
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pip check
```

The test suite does not require a live PostgreSQL or Redis service. It does not certify PostgreSQL-specific behavior, external providers, production deployment, or frontend/browser behavior.

## Frontend build

Install the JavaScript dependencies declared in `package.json` with `npm ci`, then run:

```powershell
npm run build
```

The Vite build writes Django static assets under `static/build/`; generated output is not committed. The full pytest suite includes a Chromium check that loads every named Django URL and fails on server errors, broken same-origin assets, JavaScript exceptions, or browser console errors. Install its browser once with:

```powershell
python -m playwright install chromium
```

CI installs Chromium, builds the frontend, and runs this browser sweep automatically on pushes and pull requests.

## URLs and health checks

The root URL configuration mounts the Unfold admin (`/admin/`: sidebar, dashboard, filters, actions and tabs configured in `app/settings/unfold.py`, `apps/core/admin_base.py` and `apps/core/admin_navigation.py`; its own nonce-based CSP applies to the admin shell pages only), allauth accounts, and the `users`, `devices`, `firmwares`, `tags`, `ads`, `blog`, and `comments` routes (plus the integrated storage, SEO, pages, shop, marketplace and file-manager routes). Liveness is exposed at `/health/live/`; readiness checks the configured database at `/health/ready/`.

## Governance

Read `AGENTS.md` before making changes. Product approval state, app boundaries, definition of done, security expectations, and release constraints are documented under `docs/` and `.agents/`. Do not commit or push if the VS Code Problems panel contains unresolved issues.
