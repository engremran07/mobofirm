# Requirement-to-Test Traceability

This is a current coverage map, not a claim that every route or feature is tested. Requirement IDs refer to the code-derived draft catalog in `../01-product/feature-catalog.md`; product approval is pending.

| Requirement | Existing test evidence | Coverage status and known gaps |
| --- | --- | --- |
| REQ-USR | `tests/backend/users/test_users.py` | Partial: rate limits, profile ownership/creation, following identity, notification contract. Authentication lifecycle, MFA, all notification routes, and social APIs need additional coverage. |
| REQ-DEV | `tests/backend/devices/test_devices.py` | Partial: same-identity registration, successful login event, strict new-device denial. Device APIs, all policy branches, concurrency, and notification failure paths need additional coverage. |
| REQ-ADS | `tests/backend/ads/test_ads.py` | Partial: disabled fill, creative fill, click consent, rotation consent. Affiliate flows, settings/dashboard, event retention, and provider failures need coverage. |
| REQ-BLOG | `tests/backend/blog/`; `tests/backend/integrations/test_firmware_blog_inventory.py`; `tests/backend/integrations/test_blog_hubs.py`; `tests/frontend/test_blog_browser.py` | Listing/search/filters/paging, article rendering and sanitising, privacy, editorial permissions and workflow, scheduled publishing, JSON APIs, feeds/sitemap, topic generation, hubs and browser behaviour. |
| REQ-COM | `tests/backend/comments/`; `tests/frontend/test_blog_browser.py` | Screening rules, submission, threading, votes, reports, edits, moderation queue, notifications and the accessible form. |
| REQ-FW | `tests/backend/firmwares/test_firmwares.py`; `tests/backend/governance/test_workflow.py` | Partial: token expiry/status, ad gate, daily limit, hotlink block, authorized routing, and live listing that excludes unreviewed records. Catalog CRUD, file-serving signature failures, upload permissions, and remaining API/view paths need coverage. |
| REQ-TAG | `tests/backend/tags/` | Automation (curated catalog tags, idempotent sync, promotion), views and staff tools (review, confirmed merge, duplicates, keywords), tagging service and regressions. |
| REQ-GOV | `tests/backend/governance/test_workflow.py` | Partial: distinct review/publisher permissions, idempotency, correction, comments, AI tags, firmware routing, queue access. Concurrency and all invalid transition branches need coverage. |
| REQ-CORE | `tests/backend/core/test_core.py` | Partial: positive retention and dry-run scoping. Health checks, command deletion path, and logs/metrics behavior need coverage. |

## Current suite inventory

The current root suite contains 58 passing test cases across these nine applications. Several larger apps have only partial coverage. Treat the gaps above as open quality work; do not close them without focused tests and a product-approved acceptance map.

All automated test discovery is rooted in `tests/backend/` and `tests/frontend/`. No application-local test directory is authoritative. Firmware-linked blog details pass live catalog rows to the view context; the current checkout contains no templates, so browser-visible rendering remains gated on approved frontend design.

## Change rule

For each new requirement, record an ID, owning app, acceptance criterion, and specific test before implementation. Update this map when tests or scope change. A whole-suite pass does not replace requirement-level evidence.
