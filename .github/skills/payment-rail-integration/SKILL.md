---
name: payment-rail-integration
description: Assess or implement an explicitly approved payment-provider change while preserving Mobofirm's order, wallet, and staff-review boundaries.
argument-hint: "[provider]"
---
# Payment Rail Integration

## Current-state gate

Before proposing code, inspect `docs/04-backend/django-app-boundaries.md`,
`docs/04-backend/wallet-economy.md`, `apps/shop/services.py`, and the owning
tests. Online payment processing is currently disabled: shop orders are
staff-fulfilled, wallet top-ups and payouts are offline staff decisions, and the
repository has no Whop/Stripe/PayPal checkout integration.

Do not treat an MCP server, provider credentials, an example, or a request to
research a rail as product approval to enable online payments. If the task lacks
explicit product approval for implementation, stop at a factual assessment.

## Approved implementation procedure

1. Define the owning app, order state transitions, currencies, user-facing
   behavior, provider sandbox, webhook contract, idempotency key, retry and
   terminal-failure behavior, audit record, and rollback/refund semantics before
   coding.
2. Keep the provider behind an app-owned contract. Do not change wallet
   invariants: use `apps.wallet.api`; never mutate balances or ledger entries
   directly. Preserve the existing shop fulfillment and authorization boundaries.
3. Use only provider sandbox/test mode and isolated test fixtures. Never use
   production MCP access or real payment credentials for development or tests.
4. Verify signatures before processing, persist and deduplicate provider event
   identifiers, lock and re-check the order, and validate amount/currency before
   fulfillment. Surface invalid signatures and mismatches explicitly.
5. Add focused tests for valid processing, invalid signatures, amount/currency
   mismatch, duplicate delivery, concurrent processing, rollback, and refund
   behavior. Never make external HTTP calls in tests.
6. Report approval evidence, changed behavior, exact verification, and any
   unimplemented provider or payout workflow. Do not describe a research-only
   profile as an implemented payment rail.
