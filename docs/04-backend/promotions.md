# Promotions (`apps.promotions`)

A promotion is a time-boxed campaign that ties together **announcements, entitlements, wallet rewards and ads**. It never keeps balances, quotas or prices of its own beyond the campaign's discounted figures: bonus quota flows through the shop entitlement resolver, reward boosts through the wallet reward modifier, credits through the wallet ledger and packages through the shop service.

Offer pages live under `/offers/` (`promotions:index`, `promotions:offer`, `promotions:claim`). The AppRegistry flag is `promotions_enabled`; with it off every page is a 404 and every panel renders nothing.

## Model

`Promotion` has tabs in the Unfold admin (Offer, Schedule & banner, Package & price, Bonus & rewards, Earn rules, Caps & budget, Analytics + audit log).

| Kind | What it gives | Taken by |
| --- | --- | --- |
| `free_tier_grant` | `target_package` for `trial_days` (or until the end date), via `shop.services.activate_package(source="promotion")` | claim |
| `discounted_package` | `discounted_price` / `discounted_credit_price` on `target_package` | automatic at checkout |
| `bonus_quota` | `bonus_files` and/or `bonus_size_gb` added to the download quota | claim |
| `bonus_devices` | `bonus_devices` extra device slots | claim |
| `earn_by_contributing` | the package / bonus / credits of the promotion once the earn rules are complete | automatic (or the claim button when it was deferred) |
| `reward_multiplier` | multiplier and/or flat `bonus_credits` on the wallet reward events in `event_keys` | automatic |
| `referral_boost` | same as a multiplier; `event_keys` defaults to `referral.verified` | automatic |
| `credits_bonus` | `bonus_credits` once per claim, via `wallet.credit(reason=PROMOTION)` | claim |

Audiences: `everyone`, `new_users` (joined in the last 30 days), `sellers` (approved marketplace sellers), `package_holders` (live non-default package) and `verified_email`. Every grant also needs a **verified email**.

Caps: `max_claims_total`, `max_claims_per_user`, `budget_credits` (credits the promotion may hand out, bonus grants and reward uplifts together). 0 means unlimited.

Earn rules (validated JSON, `apps/promotions/rules.py`):

```json
{"events": ["contribution.approved"], "count": 3, "within_days": 30}
```

`events` are wallet reward event keys (counted when the wallet really credits the reward, so its caps and anti-abuse already applied) or `contribution.approved`. List one or the other for the same action: an approved firmware upload is both `contribution.approved` and the wallet event `firmware.approved`, and they use different idempotency references.

Other models: `PromotionGrant` (unique per `(user, promotion, trigger)`: `claim`, `earn:<cycle>` or `order:<id>`), `PromotionProgress` (unique per `(promotion, user, ref)`) and the append-only `PromotionLog` audit.

## Lifecycle

`draft → scheduled → active → ended`. Staff move a promotion with the admin dialogs (Schedule, Start now, End now, Back to draft, Duplicate); everything else is time-driven. `lifecycle.reconcile` is idempotent and runs from the Celery beat task `apps.promotions.tasks.sync_statuses` (every minute) and from the `post_save` receiver. Scheduling validates the whole promotion (`full_clean`). Ending early expires grants that have no trial length and cancels the packages they granted; trial grants keep running.

## Announcements

While a promotion is active with `announce=True` its announcement (`users.Announcement`, linked 1:1 from `Promotion.announcement`) is published: a global banner for the audience, a CTA to `/offers/<slug>/?src=banner`, and **one** in-app notification to each audience member (resumable batches with `notify_cursor`/`notified_at`; muted "system" notices are skipped; there is no email digest because the notification service has no email channel). Ending the promotion deactivates the announcement.

`apps/users` owns the banner: `services/announcements.py` (`register_announcement_gate`, `register_announcement_view_hook`), the `announcement_banners` context processor, `templates/users/includes/announcement_banner.html` and the dismiss endpoint (a 30-day first-party cookie). Promotions registers a gate (promotion running, audience matches, hidden once claimed) and an impression hook (once per signed-in session).

## Plug-ins into other apps

* **Entitlements** – `effects.entitlement_modifier` is registered with `apps.shop.entitlements.register_entitlement_modifier`. It adds live grants and records them in `Entitlements.sources`, e.g. `Campaign "Launch bonus" +10 files until Dec 01`.
* **Wallet** – `effects.reward_modifier` is registered with `apps.wallet.api.register_reward_modifier`. The best multiplier applies (multipliers never stack), flat bonuses add up, each promotion's budget is respected and the change is recorded in the ledger row snapshot. It is a pure function; the analytics counters are settled from `wallet.credited` once the credit really happened.
* **Prices** – `api.effective_price(user, package)` returns a `PriceQuote`; `api.redeem_price(user, package, reference=...)` records the redemption once per order. `apps/shop/promotion_gateway.py` is the shop's seam (used by `create_order`, `pay_package_with_credits`, the pricing page and package detail).
* **Ads** – `ads.Campaign.promotion` (nullable) links a creative campaign to an offer; a linked campaign only serves while the offer is running and exposes `offer_url`.
* **Panels** – `api.offers_for_user(user)` feeds the My files quota panel and the wallet "Ways to earn" page (read through `AppService.get("promotions")`).
* **Search** – `search_providers.OfferProvider` (kind `offer`, running and recently ended public offers).
* **Dashboard** – the "Campaigns running" KPI on the Unfold index.

## Earn-by-contributing

`firmware.approved` (payload `uploader`, `reviewer`, `pending_id`) counts as `contribution.approved` once per `pending_id`; the generic `contribution.approved` event counts only when its payload carries a `ref`. Anti-abuse: verified email only, audience respected, items the user approved themselves never count, one counted event per object, per-user/total/budget caps, and a reward that cannot be applied (the user holds a different package) is deferred and can be taken later from the offer page.

## Claim dialog

`ClaimDialog` (`apps/promotions/dialogs.py`, template `templates/promotions/dialogs/claim.html`) is built on the core dialog shell. GET shows the effect, audience and terms; POST goes through `grants.claim` / `grants.claim_earned`, which re-check everything on the server. A rule violation (`ClaimError`) is shown inside the dialog. Claims always act on the signed-in account; there is no id in the request.

## Operations

* `manage.py seed_launch_offers` creates the "Launch bonus" and "Contribute & unlock premium" offers as **drafts** (idempotent). Review and schedule them in Campaigns > Promotions; the earn offer needs a reward package.
* Migrations: `promotions/0001_initial`, `core/0007_appregistry_promotions_enabled`, `users/0007_announcement_cta`, `ads/0007_campaign_promotion`.

## Dependencies (for `app-dependencies.md`)

promotions → `users` (announcements, notifications, `request_user`), `core` (events, app registry, dialogs, search, admin base), `shop` (lazy: `PackageTier` FK, `entitlements`, `services`), `wallet` (lazy: `credit`, reward modifier), `marketplace` (lazy: `is_active_seller`, seller profiles). Reverse seams: `shop.promotion_gateway`, `ads.Campaign.promotion`, `users.library` and `wallet.views` through `AppService`.

## Tests

`tests/backend/promotions/` (lifecycle, grants and caps, effects and discounts, earn progress, pages/dialog/banner/panels/search/ads/seed, admin) and `tests/frontend/test_promotions_browser.py` (Playwright: offer pages and banner at three widths, banner → offer → claim dialog, banner dismissal).
