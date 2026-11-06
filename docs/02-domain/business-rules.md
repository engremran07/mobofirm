# Business Rules (Implemented-State Baseline)

These rules describe behavior visible in the current code. They are not a substitute for product-owner approval.

## Accounts and roles

- `users.CustomUser` is the configured authentication model; allauth is configured for email login.
- The model defines `admin`, `editor`, `author`, `moderator`, and `reader` role values. These choices do not establish a universal permission hierarchy; authorization must use the relevant app permission/check.
- Profile, following, and activity models/API behavior are owned by `users`.

## Content moderation

- Moderation is app-owned (owner decision: the generic governance app was removed; the four-eyes submitter/reviewer/publisher separation no longer applies).
- Firmware uploads (`firmwares.PendingFirmware`) are `pending`, `approved` or `rejected`; staff with the review permission decide, the decision records reviewer, timestamp and notes, and approval routes the file to its typed table with `is_verified=True`. A rejection needs a reason; an approved upload can be withdrawn back to pending. Decisions are idempotent.
- A firmware record is public only when it is active and verified; every listing, count, search, sitemap and API goes through `firmwares.catalog`.
- Firmware is either stored (`source_type=stored`, a path under `FIRMWARE_STORAGE_ROOT`) or external (`source_type=external`, an HTTPS URL on `FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS`); exactly one of path or URL is set (database constraint).
- Blog posts move draft → in review → published/scheduled/archived through `blog.services.workflow`; comments are pending/approved/rejected/spam and clean comments are approved automatically by deterministic screening; tags from catalog facts are curated automatically and text-derived tags are promoted automatically once enough live posts use them.
- Brand, model and chipset blog posts are automated: one per entity, keyed by `firmwares.AutomatedBlogLink`, authored by the non-login automation user, published automatically and refreshed (idempotently, by content hash) whenever firmware is added, approved, changed or removed.

## Comments, posts, and tags

- Comment visibility depends on the moderation/approval state (`approved`).
- Blog publication can trigger tag suggestion/attachment; repeated assignment is expected to be idempotent.
- AI-suggested tags stay uncurated until staff approve them; uncurated tags are never attached to published content.

## Devices and access

- Device identity is resolved from user-agent and device payload inputs, with a configurable server-side fallback.
- Device-level limits, strict handling of new devices, MFA escalation, and risk thresholds are policy/configuration-driven.
- A rejected device-policy decision is recorded as a failed event and a security-policy event. Successful login events are not policy violations.

## Firmware and advertising

- Firmware download authorization is handled by the `firmwares` app, including token status/expiry, optional daily limits, ad-gate completion, and hotlink checks.
- Ad delivery and affiliate behavior belong to `ads`. Consent and enablement checks are enforced at relevant endpoints; do not infer consent from client-supplied values without server-side validation.
- Exact firmware provenance, ad eligibility, quota exceptions, and business monetization rules require product/security owner approval.

## Retention

- `cleanup_old_data` targets expired Django sessions, completed firmware download sessions, and old ad events.
- `--days` must be positive; `--dry-run` reports matches without deletion.
- This command is not a general log or file purge and must not be pointed at unapproved data.
