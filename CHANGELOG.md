# Changelog

Notable project changes are recorded here. Versions follow Semantic Versioning, and release notes follow Keep a Changelog.

## [Unreleased]

### Added

- Establish a changelog-driven Semantic Versioning and tested GitHub release workflow.
- Add production deployment assets: multi-stage `Dockerfile`, `docker-compose.yml` (web, worker, beat, PostgreSQL, Redis, nginx), nginx and gunicorn configuration, `.dockerignore`, database backup/restore scripts, `scripts/dev_up.ps1`, and `docs/08-operations/deployment.md`.
- Add `ensure_system_user` and `bootstrap_site` management commands, a Celery beat retention schedule, API token expiry/rotation, and a `public_display_name` user property that never exposes an email address.
- Install the correlation-ID, maintenance-mode, password-reset-throttle and profile-completion middleware, and enforce device registration quotas, expiry and strict/MFA locking modes.
- Install and integrate `storage`, `seo`, `pages`, `shop`, `marketplace`, `filemanager` and the central `site_settings` app (runtime branding, contact, email, reCAPTCHA, download gate, analytics and verification files in one cached accessor).
- Add the Autonomous SEO Control Center in `apps.seo` (one metadata resolver, SafeFetcher, crawler and audit, sitemap/robots/IndexNow, change ledger with rollback), the `Chipset` model with chipset and brand pages, external-download firmware, and automatic per-model, brand and chipset blog posts.
- Replace the Django admin skin with `django-unfold`: grouped sidebar, KPI dashboard, filters, actions and a test that loads every registered model.
- Add the `seed_platform` command (22 brands, 131 models, 262 firmware listings, tools, guides, articles, comments) and `scripts/reset_dev_db.ps1`.
- Poll the email-verification pages every five seconds so they continue on their own once the address is verified.
- Add strict mypy (django-stubs) alongside Ruff.

### Removed

- Remove the `apps.governance` Django app; moderation is now app-owned (firmware, blog, comments and tags).

### Changed

- Production settings now validate secure cookies, HTTPS origins, shared Redis cache, mail backend, encryption keys and proxy topology at startup; `/health/ready/` also checks the cache and returns only `ok`/`unavailable`.
- Country detection uses a trusted edge header instead of sending visitor IPs to a third-party service.

### Fixed

- Fix `MAILERS` options so production email sending works on Django 6.1, and give CI a test-only secret key.
- Fix device behavior insights (they were never recorded) and cumulative device risk scores.
- Fix stacked cards overlapping the footer (the firmware detail page) and the interests checkbox group overflowing on the profile page.
- Fix `select_for_update` on a nullable join that crashed comment notifications on PostgreSQL.

### Security

- Resolve client IPs without trusting `X-Real-IP` or client-supplied `X-Forwarded-For`; make rate limits atomic and per client address.
- Restrict DRF default authentication to sessions, remove Basic authentication, and require explicit expiring token authentication; apply MFA attempt limits and device policy to the API and Google sign-in paths.
- Close or redirect the stock allauth login, signup and Google token routes, require reCAPTCHA tokens when enabled, and fix open redirects, MFA disable and inactive-user checks.
- Harden ad creative validation, ad event trust, and the ads settings toggle.
