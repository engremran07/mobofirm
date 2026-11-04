# Business Rules (Implemented-State Baseline)

These rules describe behavior visible in the current code. They are not a substitute for product-owner approval.

## Accounts and roles

- `users.CustomUser` is the configured authentication model; allauth is configured for email login.
- The model defines `admin`, `editor`, `author`, `moderator`, and `reader` role values. These choices do not establish a universal permission hierarchy; authorization must use the relevant app permission/check.
- Profile, following, and activity models/API behavior are owned by `users`.

## Content governance

- The governance workflow accepts `blog.Post`, `comments.Comment`, `firmwares.PendingFirmware`, and `tags.Tag`.
- Only one open review case may exist for a given content object.
- Submission, review, decision, fulfillment, and corrective action are represented as separate workflow steps. Review and publication/fulfillment require their respective permissions; workflow tests establish separation for covered paths.
- Governance events are append-only and use an idempotency key. A repeated action with the same key must not duplicate fulfillment.
- Corrective reports can move completed content back into a correction/review state; exact product/service-level policy remains unapproved.

## Comments, posts, and tags

- Comment visibility depends on the moderation/approval state; governance fulfillment is a covered approval path.
- Blog publication can trigger tag suggestion/attachment; repeated assignment is expected to be idempotent.
- AI-suggested tags enter governance review rather than being treated as curated solely because they were suggested.

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
