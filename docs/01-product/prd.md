# Product Requirements Document (Draft)

**Status: draft.** The overall product scope, user journeys, and business/security policies remain subject to product-owner approval. The product owner has separately approved the initial frontend stack and hybrid public/admin architecture recorded in `../05-frontend/design-system.md`.

## Problem statement

The repository contains a backend intended to organize firmware-related information and supporting community and governance workflows. The exact target audience, business model, service levels, and launch scope are not established in the current repository evidence.

## Candidate capability areas

1. User accounts, profile data, notifications, and account security.
2. Device identity records and device access policy.
3. Firmware catalog, upload/review, download authorization, and release information.
4. Blog posts and comments.
5. Tag discovery and curation.
6. Ad placements/events and affiliate product links.
7. Auditable review, decision, fulfillment, and correction workflows.
8. Operational health and bounded retention utilities.
9. Runtime site settings an operator edits without a deploy: branding, contact and social links, maintenance mode, email credentials, reCAPTCHA, download gate, analytics and search-engine verification (`site_settings`, see `../04-backend/site-settings.md`).

These are a code-derived inventory, not approved feature requirements. The detailed implementation catalog and current tests are in `feature-catalog.md` and `../06-quality/requirements-traceability.md`.

## Non-goals until approved

- Expanding frontend scope or changing the approved stack/architecture without a new owner decision.
- Introducing placeholder app integrations. The owner decision of this release integrates `storage`, `seo`, `pages`, `shop`, `marketplace`, `wallet` and `referral` as first-class installed apps (see `docs/04-backend/django-app-boundaries.md`), together with `consent`, `gamification`, `bounty` and `distribution`. `consent` is the only source of cookie/tracker consent (analytics and advertising load only after opt-in); `bounty` pays from wallet escrow; `distribution` shares through plain HTTPS channels that are off by default. `wallet` is an internal credits ledger (earn by contributing, spend on packages and files; offline staff-confirmed top-ups and payouts); online payments, payment gateways and Stripe remain explicitly out of scope. Personal file storage (folders, user uploads, share links) is out of scope by owner decision.
- Promising particular download availability, security guarantees, revenue outcomes, or service levels not evidenced by approved requirements.

## Approval checklist

- [ ] Product owner approves target personas and core journeys.
- [ ] Product owner approves feature scope and acceptance criteria.
- [ ] Security/data owners approve identity, download, retention, and consent policies.
- [x] Product owner approved the initial frontend stack and hybrid shared-asset architecture.
- [ ] Design owner finalizes visual identity, brand assets, and detailed responsive/accessibility specifications.
