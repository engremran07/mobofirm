---
description: "Use when changing wallet, shop, bounty, marketplace, or promotion code that handles value or entitlements."
applyTo: ["apps/wallet/**/*.py", "apps/shop/**/*.py", "apps/bounty/**/*.py", "apps/marketplace/**/*.py", "apps/promotions/**/*.py"]
---
# Credits, Orders, and Entitlements

- Treat `apps/wallet.api` as the wallet integration boundary. Never update
  `Wallet.balance` or `Wallet.held` outside wallet services and `ledger_posting()`.
  `LedgerEntry` is append-only; do not update or delete it.
- Every credit/debit/reward/spend/refund/escrow operation needs a globally unique,
  namespaced idempotency key. Preserve the existing lock order and make related
  ledger and balance changes atomic.
- Use the existing shop order and entitlement services. Online payments are
  disabled; do not add or enable Whop, Stripe, PayPal, or other payment rails
  without explicit product approval and a separate, test-mode integration design.
- Top-ups and payouts are offline, staff-reviewed workflows. Do not expose
  financial approvals to MCP tools or automate them through an agent.
- Verify authorization, invalid amounts, duplicate delivery, concurrency, rollback,
  and ledger reconciliation behavior in focused tests. Never use live financial
  data or provider accounts for tests.
