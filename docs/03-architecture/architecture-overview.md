# Architecture Overview

## Status and deployment boundary

This is a Django monolith organized into a project package and separately owned app packages. `app/` contains Django bootstrap/configuration; `apps/` contains domain and shared-platform applications. PostgreSQL is the configured runtime database. Tests use in-memory SQLite. Celery/Redis support exists for asynchronous work; Redis is not needed for the test suite.

The approved frontend foundation is Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide, and Vite. Public and Django admin experiences use distinct shells: the public shell shares one token/static-asset source, and the admin is django-unfold (`app/settings/unfold.py`, `apps/core/admin_base.py`) themed from the same brand tokens. Keep per-app additions namespaced; final visual calibration remains a product/design review item.

Every `apps/*/admin.py` builds on `apps.core.admin_base`; the sidebar (`apps/core/admin_navigation.py`) must list every registered model, and the admin is served under its own CSP (`ADMIN_CSP`, Unfold shell pages only). Details: `../05-frontend/design-system.md` ("Admin console (Unfold)").

## Runtime shape

```text
HTTP / Django ASGI or WSGI
             |
        app/urls.py
             |
  +----------+-----------------------------------------------+
  | users | devices | firmwares | ads | blog | comments      |
  | tags  | core | storage | seo | pages                      |
  | shop  | marketplace | site_settings | wallet | referral   |
  | consent | gamification | bounty | distribution           |
  | promotions                                                |
  +----------+-----------------------------------------------+
             |
       Django ORM / PostgreSQL
             |
      Celery broker (Redis when workers are used)
```

`app/settings/base.py` configures the installed apps, authentication, middleware, PostgreSQL, static/media roots, and Celery defaults. `app/settings/test.py` overrides the database with in-memory SQLite and runs Celery eagerly. `app/settings/production.py` requires production secret/database settings and enables HTTPS-related security settings.

## Integration seams

- Moderation is app-owned; there is no `governance` Django app (it was removed by owner decision and `tests/backend/integrations/test_governance_app_removed.py` keeps it removed). Firmware moderation (pending/approved/rejected with reviewer, timestamp and reason) lives in `firmwares`, the post status workflow in `blog`, comment decisions in `comments` and tag curation in `tags`; each is operated by staff holding the relevant model permission. "Governance" is documentation only (`docs/00-governance/`).
- `users` owns identity/profile/notification state and exposes user API/routes.
- `devices` owns operating-system registrations (a platform WebAuthn credential shared by every browser on that OS and destroyed by a reinstall), fallback browser-profile registrations, trust/revocation state, device policy, and registration events. It issues a random first-party browser key (stores only a keyed digest) used as the fallback where no platform authenticator is usable; a website cannot read hardware or an install id, so the credential is the closest web-only OS identity.
- `users` authentication calls the Devices service contract for login policy; Users does not own device registration or trust state.
- `firmwares` owns firmware catalog/download lifecycle and its own staging and approval services (staged content stays pending until staff approve it). Generated blog guides use the blog app's history/review services; an automation setting may request review, never direct publication.
- `blog`, `comments`, and `tags` are connected through content ownership, moderation, and tagging.
- `blog` owns editorial revisions in the migrated `PostRevision` model; a draft restore never changes publication state.
- Automated tag suggestions remain uncurated until staff approve them in `tags` and are not attached to public content until curated.
- `ads` owns ad and affiliate data; consumers must use the app's service/API contracts.
- `consent` owns cookie/tracker consent and exposes only `has_consent(request, category)`; `ads` and the `site_settings` analytics loader ask it. `gamification` and `bounty` react to `firmware.approved` and wallet events on the event bus and reach the wallet only through `apps.wallet.api`. `distribution` listens to `firmware.approved` and the blog publish signal and sends everything through the SEO SafeFetcher.
- `storage` records provider, location and integrity for typed firmware through ContentType; it reuses the firmware external-host allow-list and never serves files. `seo` owns the sitemap registry and routes (`/sitemap.xml`, `/robots.txt`) and the SEO settings flags; `pages`, `shop` and `marketplace` are feature-flagged through `core.AppRegistry`.
- `core` owns shared support and health/retention utilities, not a second copy of domain state.
- `wallet` is the only place credits live. Apps earn through `apps.wallet.api.grant_reward` and spend through `spend`/`refund` (idempotent on a caller key, atomic under a wallet row lock); `referral` rewards through the same contract and subscribes to the `contribution.approved` and email-verified events.
- `site_settings` is the single runtime settings authority for site-wide values (see `docs/04-backend/site-settings.md`). Every consumer reads `apps.site_settings.services.get_site_settings()` (cached, invalidated on save; Django/environment settings are the defaults) and never copies those values; settings that another app owns stay with that app.

Treat signal/task behavior as an explicit integration contract: preserve idempotency and after-commit semantics where required, and test observable downstream state.

## Directory decision

The current sibling layout (`app/` and `apps/`) is appropriate. Keep `app/` small and free of domain models. Keep each domain app under `apps/`, with project settings importing only actual installed apps. Do not nest the apps inside `app/` or create an umbrella app containing duplicate domain state.

## Unapproved choices

Final visual identity/calibration, deployment platform, object-storage provider, tenant model, and production observability backends are not established by this architecture baseline.
