# Acceptance Criteria (Draft)

**Status: draft; product owner approval required.** Criteria below capture safety expectations for current capability families. They do not define a final UI or complete product contract.

| ID | Draft observable criterion |
|---|---|
| REQ-USR-AC1 | A user cannot retrieve or create another user's private profile through the profile API; the authenticated identity is authoritative for follower actions. |
| REQ-DEV-AC1 | Repeated registration for the same user and device identity does not create duplicate device records. |
| REQ-DEV-AC2 | A policy-denied login is rejected and leaves an auditable failure; an allowed login does not create a policy-violation record. |
| REQ-ADS-AC1 | Disabled ad delivery is rejected, and consent-sensitive click/event actions honor the configured consent state. |
| REQ-BLOG-AC1 | Publishing a governed post does not bypass its required review/fulfillment behavior; tag automation is idempotent. |
| REQ-COM-AC1 | Non-approved comments are not exposed as approved content; moderation actions preserve the configured status contract. |
| REQ-FW-AC1 | Download tokens, expiration, daily limits, ad gates, and hotlink checks are enforced at the server boundary. |
| REQ-FW-AC2 | A firmware-linked blog post displays the current active firmware records that are governance-routed or explicitly verified. New eligible records appear without rewriting or republishing the post body; unreviewed records are excluded. |
| REQ-TAG-AC1 | Tag assignment and synchronization are idempotent; tag merges preserve valid relationships. |
| REQ-GOV-AC1 | A submitted item has one open review case; reviewers and publishers satisfy distinct permissions, and repeated fulfillment is idempotent. |
| REQ-CORE-AC1 | Retention accepts only positive ages, affects only the explicitly scoped record types, and dry-run mode deletes nothing. |

These criteria cover selected code-level behavior, not all routes, edge cases, accessibility, operations, or product outcomes. `../06-quality/requirements-traceability.md` records current test evidence and known gaps.
