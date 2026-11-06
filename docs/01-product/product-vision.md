# Product Vision (Draft)

**Approval status: draft.** This document describes the apparent product direction from the current code, not an authorized commitment for the full product scope. The frontend foundation is separately owner-approved as recorded in `../05-frontend/design-system.md`.

## Intent

Provide a maintainable platform for managing mobile-device firmware information and downloads, with supporting user accounts, device identity controls, editorial content, comments, tags, advertising, and auditable content review.

## Current capability evidence

The installed Django apps implement portions of these domains: `users`, `devices`, `firmwares`, `ads`, `blog`, `comments`, `tags`, and `core` (plus the integrated storage, SEO, pages, shop, marketplace and file-manager apps). The route and data inventory is in `../03-architecture/architecture-overview.md` and `../04-backend/django-app-boundaries.md`.

## Product decisions still needed

- Primary audiences and top user journeys.
- Which capabilities are launch-critical, optional, or out of scope.
- Firmware provenance, quality standards, licensing, and publisher responsibilities.
- Account verification, device identity, MFA, and download-access policy.
- Advertising, affiliate, consent, and analytics policy.
- Editorial ownership and moderation service levels.
- Final visual identity, brand assets, and detailed accessibility specifications.

The approved frontend foundation may be implemented without inventing new user-facing business policy. Other product decisions remain open until approved.
