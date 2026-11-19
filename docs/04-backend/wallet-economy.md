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

`grant_reward(user, event_key, *, key, reference="", amount_override=None, context=None) -> LedgerEntry | None`

- Idempotent on `key` (globally unique, namespace it: `firmware:<uuid>:approved`). Repeating a call returns the original entry, even if the rule changed since.
- Applies the rule: enabled, verified email, minimum account age, cooldown, daily/monthly/lifetime caps (in credits per user; "daily" and "monthly" follow the project time zone), one payout per `reference` object. Caps must not shrink with a longer window (daily ≤ monthly ≤ lifetime, 0 = no cap; validated in the admin).
- `context={"source_user_id": …}` refuses self-rewards. `amount_override=` overrides the rule amount (bounties, per-badge credits); caps still apply.
- Never raises for a business refusal: it returns `None` and records a `RewardRefusal` (Credits economy > Abuse flags).
- `register_reward_modifier(fn)` lets campaigns change the amount: `fn(user, event_key, credits, context) -> int` (`unregister_reward_modifier(fn)` ends it). Modifiers are recorded in the entry `snapshot`.
- `reward_rules(event_keys) -> dict[str, RewardRuleView]` gives other apps' pages a read-only copy of live rules (credits, enabled, daily cap) without importing the model.

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
`blog.featured_purchase`) get their credits passed to `spend(price_override=…)` and show "dollars × rate" in the admin.
A sink may also declare `user_price=fn(user) -> int | None` for prices that differ per account (the slot reset:
package override → global price), which **Ways to spend** shows to each user.

## Spend

Declare sinks in `apps/<app>/spend_sinks.py`: `register_spend_sink(SpendSink(key="package.purchase", label=…, app=…, default_credits=0))`.

- `price_of(sink_key, *, quantity=1, context=None, price_override=None) -> int` (admin `CreditPrice` × quantity, or `price_override` when the owner derives the price, then `register_price_modifier` discounts; `unregister_price_modifier` ends one). Raises `UnknownSink` / `SinkDisabled`.
- `spend(user, sink_key, *, key, reference="", quantity=1, price_override=None) -> LedgerEntry`. Atomic under `select_for_update(of=("self",))` on the wallet row and idempotent on `key` (a replay returns the first charge, see Invariants). `price_override=` overrides the sink price (a package override, a file price); modifiers still apply. Raises `InsufficientCredits` (`.needed`, `.available`), `WalletFrozen`.
- `refund(entry_key, *, reason) -> LedgerEntry` reverses a spend once.

Packages and single files are paid with earned credits through these sinks (`package.purchase`,
`file.purchase`, `apps/shop/credits.py`) or by staff-confirmed offline orders; online gateways
stay disabled. Unlocking a file by watching rewarded ads (`docs/04-backend/entitlements.md`,
"Unlock by watching ads") is a third, credit-free route: it moves no ledger entry and the
`download_unlock` reward type never calls the wallet.

## Ledger and escrow

- `balance(user) -> int` spendable credits; `balance_snapshot(user)`; `withdrawable_balance(user)` (earned from sales and bounties, not yet paid out).
- `rewards_earned(user, event_prefixes) -> int` read-only total of reward credits paid for events whose key starts with a prefix (`("firmware.", "blog.")` feeds the contribution summary on My contributions; it never creates a wallet).
- `credit` / `debit(user, credits, *, reason: LedgerReason, key, reference="", note="")` for flows that are not rewards (seller earnings use `LedgerReason.EARNING`).
- `lock_escrow(user, credits, *, key, reason="", reference="")` reserves credits; `cancel_escrow(hold)` frees them; `release_escrow(hold, *, to_user, key, event_key="bounty.fulfilled")` pays the recipient through `grant_reward` in one transaction.
- `to_credits(amount: Decimal) -> int` (half up) and `credits_per_currency()` read `WalletSettings`, the single admin-editable settings row.

## Top-ups and payouts

- `request_topup(user, credits)` creates a pending `TopUpRequest`; staff confirm payment in the admin (or the shop publishes event `shop.order_paid` with `{"order_id": n}` for an order stored in `TopUpRequest.order_reference`). If `apps.shop.api.create_credit_topup_order(user, *, credits, price, reference)` exists the wallet asks the shop for an order.
- `request_payout(user, credits, *, method, destination)` holds the credits at once; staff approve, mark paid, or reject (credits return). Only earned credits can be paid out.

## Invariants

The ledger is append-only and `Wallet.balance` always equals credits minus debits; `Wallet.held` equals the open escrow holds. Wallets and ledger rows are `PROTECT`ed, so a user with financial history cannot be hard-deleted (see "Account erasure").

Layers, from the code outwards (F-020):

1. **Model.** `LedgerEntry.save()` only inserts (a second save, or a new object that reuses a primary key, is refused), `delete()` and queryset `update`/`delete`/`bulk_update`/`bulk_create` raise `TypeError`. `Wallet.balance`/`held` can only be written inside `ledger_posting()` (opened by the services and the audited repair): `Wallet.objects.update(balance=…)`, `bulk_update` and a full `wallet.save()` of a possibly stale copy are refused.
2. **Database.** Constraints: `amount > 0`, `direction in (credit, debit)`, non-empty `key` (unique), `held <= balance`, non-negative counters (the positive-integer column check), non-negative top-up/payout money and a 0–100 payout fee. On PostgreSQL migration `0003_ledger_integrity` also installs a `BEFORE UPDATE`/`BEFORE DELETE` trigger (`wallet_ledger_guard`) that refuses any change to a money column and any delete; `actor_id` can only become NULL (a staff account's `SET_NULL`) and `note` changes only inside the erasure window (`set_config('wallet.ledger_scrub', 'on', true)`). The migration is reversible (it drops the triggers and the function) and a no-op on other backends. `TRUNCATE` is deliberately not guarded (test flush, full restore). A separate low-privilege database role for the app is an infrastructure decision, not done here.
3. **Reconciliation.** `reconcile()` recomputes every wallet from the ledger and the open holds (see below).

Idempotency: every credit, debit, reward, spend, refund, escrow settlement, top-up and payout posts under a globally unique `key`. Repeating a key returns the stored entry and changes nothing, even if the price, a discount, the rule or the sink's enabled flag changed since; reusing a key for another wallet, direction, amount (credit/debit), reason or sink raises `IdempotencyConflict`. `spend` answers a replay before pricing it, so a duplicate shop delivery never charges twice and never fails a buyer who already paid.

Concurrency: one lock order everywhere (request/hold row, then wallet rows in ascending primary key via `lock_wallets`, then the reward-rule row for per-object rewards), so releases in opposite directions cannot deadlock. Decisions are taken on rows read after the lock. A per-object reward ("this firmware pays once") locks its rule row, which makes the promise hold across different recipients. Caps and cooldowns are counted under the wallet lock, so parallel requests cannot exceed them. PostgreSQL tests: `tests/backend/wallet/test_wallet_postgres.py`.

## Reconciliation and repair

- `reconcile(wallet_ids=None, batch_size=500, deep=False, max_seconds=None) -> ReconcileReport` (read-only): `balance` must equal credits minus debits, `held` the open escrow holds, and the last entry's `balance_after` the balance; `deep` also walks each ledger. A candidate is re-checked under the wallet lock before it is reported, so a posting that commits mid-run is never a false alarm. It never changes money.
- Command: `manage.py reconcile_wallets [--wallet ID] [--deep] [--fail-on-drift]`.
- Task: `apps.wallet.tasks.reconcile_wallets` (soft limit 540 s, hard 600 s, stops itself after 480 s and reports `complete=False`). Drift is logged at ERROR with structured fields (`alert=wallet_reconcile_drift`, `wallet_id`, `user_id`, `field`, `expected`, `actual`) and one summary line; a clean run logs at INFO. The beat entry is project configuration (`CELERY_BEAT_SCHEDULE`, request filed).
- Repair is a separate act: `manage.py repair_wallet WALLET_ID` is a dry run; `--apply --actor LOGIN --reason TEXT --key KEY` rewrites `balance`/`held` from the ledger (never the reverse), needs `wallet.adjust_wallet`, is idempotent on the key and leaves a `WalletRepair` row (Credits economy → Wallet repairs). It refuses when the ledger itself is inconsistent.

## Account erasure

`apps.wallet.api.erasure_blockers(user)` lists what must be resolved first (open payouts, escrow holds). `anonymise_user(user) -> ErasureReport` then clears the personal free text around the accounting data in one transaction: ledger notes, top-up payment references, payout destinations/references/decision text, reward-refusal details, the frozen reason, and unlinks the user as a staff actor. Rows, amounts, keys and running balances stay, so every balance still reconciles; it is idempotent and never creates a wallet. The `users` erasure workflow owns the account row and calls this contract.

## Money

Amounts are `Decimal` (floats are converted through `str`, never through binary). Conversion and display round half up (`format_currency` quantises to cents first because `format(Decimal, ".2f")` rounds half-even); a positive price is never below one credit; negative, non-finite and non-numeric amounts are refused.

## Events

`wallet.credited`, `wallet.debited`, `wallet.escrow_locked`, `wallet.escrow_released`, `wallet.topup_confirmed`, `wallet.payout_requested`, `wallet.payout_resolved`, `wallet.reward_refused` (published after commit). Subscribed: `shop.order_paid`.
