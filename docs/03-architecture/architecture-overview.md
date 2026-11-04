# Architecture Overview

## Status and deployment boundary

This is a Django monolith organized into a project package and separately owned app packages. `app/` contains Django bootstrap/configuration; `apps/` contains domain and shared-platform applications. PostgreSQL is the configured runtime database. Tests use in-memory SQLite. Celery/Redis support exists for asynchronous work; Redis is not needed for the test suite.

The approved frontend foundation is Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide, and Vite. Public and Django admin experiences use distinct shells over a centrally maintained token and static-asset source. Keep per-app additions namespaced; final visual calibration remains a product/design review item.

## Runtime shape

```text
HTTP / Django ASGI or WSGI
             |
        app/urls.py
             |
  +----------+-----------------------------------------------+
  | users | devices | firmwares | ads | blog | comments      |
  | tags  | governance | core                                  |
  +----------+-----------------------------------------------+
             |
       Django ORM / PostgreSQL
             |
      Celery broker (Redis when workers are used)
```

`app/settings/base.py` configures the installed apps, authentication, middleware, PostgreSQL, static/media roots, and Celery defaults. `app/settings/test.py` overrides the database with in-memory SQLite and runs Celery eagerly. `app/settings/production.py` requires production secret/database settings and enables HTTPS-related security settings.

## Integration seams

- `governance` is the controlled review/fulfillment boundary for posts, comments, pending firmware, and tags.
- `users` owns identity/profile/notification state and exposes user API/routes.
- `devices` owns device identity and policy records and participates in login policy.
- `firmwares` owns firmware catalog/download lifecycle and uses governance for staged content. Generated blog guides use the blog app's history/review services; an automation setting may request review, never direct publication.
- `blog`, `comments`, and `tags` are connected through content ownership, moderation, and tagging.
- `blog` owns editorial revisions in the migrated `PostRevision` model; a draft restore never changes publication state.
- Automated tag suggestions remain uncurated until governance fulfillment and are not attached to public content until curated.
- `ads` owns ad and affiliate data; consumers must use the app's service/API contracts.
- `core` owns shared support and health/retention utilities, not a second copy of domain state.

Treat signal/task behavior as an explicit integration contract: preserve idempotency and after-commit semantics where required, and test observable downstream state.

## Directory decision

The current sibling layout (`app/` and `apps/`) is appropriate. Keep `app/` small and free of domain models. Keep each domain app under `apps/`, with project settings importing only actual installed apps. Do not nest the apps inside `app/` or create an umbrella app containing duplicate domain state.

## Unapproved choices

Final visual identity/calibration, deployment platform, object-storage provider, tenant model, and production observability backends are not established by this architecture baseline.
