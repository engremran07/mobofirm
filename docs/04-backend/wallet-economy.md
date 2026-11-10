# Credits economy (`apps.wallet`)

Credits are an internal currency: whole numbers, no fractions, no payment gateway. Users earn them by contributing and spend them on packages, paid files and device slots. Cash only moves offline: staff confirm top-ups and pay out earned credits.

Everything below is imported from `apps.wallet.api` (the same names exist in `apps.wallet.services`).

## Earn

Each app declares what it pays in `apps/<app>/reward_events.py` (autodiscovered when the wallet starts):

```python
from apps.wallet.api import RewardEvent, register_reward_event

register_reward_event(
    RewardEvent(
        key="firmware.approved",
        label="Firmware approved",
        app="firmwares",
        default_credits=25,
    )
)
```

Amounts live only in the admin-editable `RewardRule` (Credits economy > Reward rules); the declared defaults create the row once and never overwrite staff edits.

`grant_reward(user, event_key, *, key, reference="", credits=None, context=None) -> LedgerEntry | None`

- Idempotent on `key` (globally unique, namespace it: `firmware:<uuid>:approved`). Repeating a call returns the original entry.
- Applies the rule: enabled, verified email, minimum account age, cooldown, daily/monthly/lifetime caps (in credits per user), one payout per `reference` object.
- `context={"source_user_id": …}` refuses self-rewards. `credits=` overrides the rule amount (bounties, per-badge credits); caps still apply.
- Never raises for a business refusal: it returns `None` and records a `RewardRefusal` (Credits economy > Abuse flags).
- `register_reward_modifier(fn)` lets campaigns change the amount: `fn(user, event_key, credits, context) -> int`. Modifiers are recorded in the entry `snapshot`.

## Pricing: one rate, every price in dollars

Prices of things sold (packages, paid and featured files, featured blog articles, promotions)
are **US dollars**. Credits are always derived: `to_credits(amount) = amount × WalletSettings.credits_per_currency`,
rounded half up, never below 1 for a positive amount (a priced item cannot become free), 0 for 0;
negative and non-numeric amounts are refused. That is the ONLY conversion (`apps.wallet.api.to_credits`,
`to_credits_at(amount, rate)` previews a rate; `price_label(amount)` renders `$4.00 · 40 credits`). Nothing else stores a
credits copy of a dollar price: `PackageTier.credit_price` is a property (an optional,
labelled `credit_price_override` ignores the rate), and a rate change re-prices everything
at once while the ledger is never rewritten. The rate is edited on **Credits economy → Credits & pricing**,
which shows a preview table and warns about the effect.

Only sinks with a genuinely fixed price keep a `CreditPrice` row staff edit (e.g.
`device.slot_reset`, default 10). Sinks declared `derived=True` (`package.purchase`, `file.purchase`,
`blog.featured_purchase`) get their credits passed to `spend(credits=…)` and show "dollars × rate" in the admin.
A sink may also declare `user_price=fn(user) -> int | None` for prices that differ per account (the slot reset:
package override → global price), which **Ways to spend** shows to each user.

## Spend

Declare sinks in `apps/<app>/spend_sinks.py`: `register_spend_sink(SpendSink(key="package.purchase", label=…, app=…, default_credits=0))`.

- `price_of(sink_key, *, quantity=1, context=None, credits=None) -> int` (admin `CreditPrice` × quantity, or `credits` when the owner derives the price, then `register_price_modifier` discounts). Raises `UnknownSink` / `SinkDisabled`.
- `spend(user, sink_key, *, key, reference="", quantity=1, credits=None) -> LedgerEntry`. Atomic under `select_for_update(of=("self",))` on the wallet row and idempotent on `key`. `credits=` overrides the sink price (a package override, a file price); modifiers still apply. Raises `InsufficientCredits` (`.needed`, `.available`), `WalletFrozen`.
- `refund(entry_key, *, reason) -> LedgerEntry` reverses a spend once.

## Ledger and escrow

- `balance(user) -> int` spendable credits; `balance_snapshot(user)`; `withdrawable_balance(user)` (earned from sales and bounties, not yet paid out).
- `credit` / `debit(user, credits, *, reason: LedgerReason, key, reference="", note="")` for flows that are not rewards (seller earnings use `LedgerReason.EARNING`).
- `lock_escrow(user, credits, *, key, reason="", reference="")` reserves credits; `cancel_escrow(hold)` frees them; `release_escrow(hold, *, to_user, key, event_key="bounty.fulfilled")` pays the recipient through `grant_reward` in one transaction.
- `to_credits(amount: Decimal) -> int` (half up) and `credits_per_currency()` read `WalletSettings`, the single admin-editable settings row.

## Top-ups and payouts

- `request_topup(user, credits)` creates a pending `TopUpRequest`; staff confirm payment in the admin (or the shop publishes event `shop.order_paid` with `{"order_id": n}` for an order stored in `TopUpRequest.order_reference`). If `apps.shop.api.create_credit_topup_order(user, *, credits, price, reference)` exists the wallet asks the shop for an order.
- `request_payout(user, credits, *, method, destination)` holds the credits at once; staff approve, mark paid, or reject (credits return). Only earned credits can be paid out.

## Invariants

The ledger is append-only (no update or delete, at model and queryset level); `Wallet.balance` always equals credits minus debits (`audit_wallet`). Wallets and ledger rows are `PROTECT`ed, so a user with financial history cannot be hard-deleted.

## Events

`wallet.credited`, `wallet.debited`, `wallet.escrow_locked`, `wallet.escrow_released`, `wallet.topup_confirmed`, `wallet.payout_requested`, `wallet.payout_resolved`, `wallet.reward_refused` (published after commit). Subscribed: `shop.order_paid`.
