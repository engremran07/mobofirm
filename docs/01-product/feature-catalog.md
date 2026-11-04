# Feature Catalog (Code-Derived Draft)

The catalog inventories capability families visible in the installed code. It is not a complete feature specification, and all product scope remains unapproved. Test mappings indicate existing evidence, not comprehensive coverage.

| ID | Capability family | Owning app | Current evidence | Test evidence |
|---|---|---|---|---|
| REQ-USR | Account, profile, following, activity, notification, and security workflows | `users` | User routes, account model, profile/social models and notification services | `tests/backend/users/test_users.py` (partial) |
| REQ-DEV | Device identity, per-user device records, device events, and policy enforcement | `devices` | Device services, policy settings, device APIs and login signal | `tests/backend/devices/test_devices.py` (registration and login-policy paths only) |
| REQ-ADS | Ad delivery, consent-aware events/clicks, rewarded ad settings, and affiliate products | `ads` | Ad/affiliate models, API routes and services | `tests/backend/ads/test_ads.py` (partial) |
| REQ-BLOG | Editorial posts, publication workflow, feeds, search, and related content | `blog` | Post/category models, views, feeds, automation and governance integration | `tests/backend/blog/test_tagging_integration.py` (tag publication path only) |
| REQ-COM | Comment submission, visibility, reporting, voting, and moderation | `comments` | Comment model, routes, moderation services and governance integration | `tests/backend/comments/test_comments.py` (partial); governance integration |
| REQ-FW | Firmware catalog, staged upload/review, live model-linked blog inventory, downloads, gates, hotlink checks, changelogs, and guides | `firmwares` | Firmware models, services, APIs, routes and governance integration | `tests/backend/firmwares/test_firmwares.py` (download controls only); `tests/backend/governance/test_workflow.py` covers governed routing and live blog listing |
| REQ-TAG | Tag assignment, lookup, discovery, merging, and curation | `tags` | Tag and generic tagged-item models, services, views and automation | `tests/backend/tags/` (service/regression coverage; UI/API coverage incomplete) |
| REQ-GOV | Review submission, reviewer decisions, separate publication/fulfillment, and correction audit | `governance` | Supported content types, permission checks, idempotency, append-only events | `tests/backend/governance/test_workflow.py` |
| REQ-CORE | Shared platform support, health endpoints, and scoped retention command | `core` | Settings/services, `/health/live/`, `/health/ready/`, cleanup command | `tests/backend/core/test_core.py` (retention behavior only) |
| REQ-REL | Application release metadata and editorial content history/restore | `app`, `blog` | Package metadata is surfaced in public/admin shells; draft-only restore uses the existing `PostRevision` model | `tests/backend/core/test_release_metadata.py`, `tests/backend/blog/test_versioning.py` |

Do not mark a capability complete from this table alone. Expand each family into approved user scenarios and acceptance criteria before implementation or release.
