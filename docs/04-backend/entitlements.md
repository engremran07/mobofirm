# Entitlements and download packages

`apps.shop` owns one concept: the **Download Package** (`shop.PackageTier`, shown as
"Package" everywhere). One package defines price, duration, devices, bandwidth,
item types, extra and fair-usage caps and perks. `UserPackage` is the only subscription
record. A package is bought directly through an `Order`; there is no separate product
catalogue. `apps/shop/entitlements.py::effective_entitlements(user)` is the only resolver
of what an account may do; everything else reads it.

## The package

| Tab | Fields | Meaning |
| --- | --- | --- |
| Basic | `name`, `slug`, `description`, `price` (USD), `allow_credits`, `credit_price_override`, `duration_days` (0 = lifetime), `limit_devices` + `device_limit`, `slot_reset_credits`, `is_public`, `is_active`, `is_default`, `is_popular`, `sort_order` | One-off price in US dollars for the whole duration. The credits price is **derived** (`PackageTier.credit_price` = `price × WalletSettings.credits_per_currency` through `apps.wallet.api.to_credits`, 0 when credits are not allowed or the package is free); `credit_price_override` is an optional, clearly labelled fixed credits price that ignores the rate. The editor shows a live `$ · credits` readout. `slot_reset_credits` overrides the global device slot-reset price for holders (empty = global price). `is_default` marks the free package every account gets. |
| Bandwidth | `bandwidth_enabled`, `limit_files`, `limit_size` + `size_unit` (MB/GB), `window` (`package` / `month` / `day`) | New files and/or size per window. 0 = no limit on that dimension. `package` = the whole package period (or "in total" for a lifetime package); `month` and `day` are calendar windows in the server time zone. |
| Item types | `applies_to_free`, `applies_to_featured`, `applies_to_paid`, `paid_discount_percent`, `applies_to_featured_blog` + `featured_blog_limit` | The item types the package covers: they are counted in the quota, `featured` also unlocks featured files, `paid` files you buy count (never blocked) and get the discount. At least one file type is required. `applies_to_featured_blog` lets holders read featured blog articles in full; `featured_blog_limit` is the most articles they can unlock per window (0 = unlimited), counted once per article in `FeaturedBlogUnlock`. |
| Extra | `extra_daily_files`, `extra_daily_size` | Added on top of the window allowance every day: when the window is spent, the user still gets this much today. |
| Fair usage | `fair_daily_*`, `fair_monthly_*`, `max_concurrent_downloads`, `cooldown_seconds` | Service-protection caps. They count free and featured files only, apply even when bandwidth is off and never block a paid file the user bought. |
| Perks | `ad_free`, `priority_queue`, `resume_support`, `direct_links`, `early_access` | `ad_free` is enforced by the download token (no ad gate); the others are exposed on `Entitlements.perks`. |

`PackageTier.summary()` is the single human description (admin list, admin preview tab,
pricing page, My package): `30 files / 20 GB per 30 days · free + featured · 5 devices · $9/mo`.
Validation lives in `PackageTier.clean()` and the model constraints (at least one item type,
price >= 0, positive device cap, one default package, default package free + lifetime +
active + day/month window, bandwidth needs a limit, discount needs the paid type, monthly
fair cap >= daily cap).

## Resolution

`effective_entitlements(user) -> Entitlements` (frozen dataclass) resolves, in order:

1. The default package (or, when none is active, a transient free package built from
   `SHOP_FREE_DAILY_DOWNLOADS`, `SHOP_FREE_MONTHLY_DOWNLOADS`, `SHOP_FREE_DEVICE_LIMIT`).
   Every account also holds a **baseline** `UserPackage` (`is_baseline`, source `default`)
   pointing at it: created by the `post_save` receiver of `apps/shop/receivers.py` for every
   new account (allauth e-mail and social sign-up, admin, commands), backfilled for
   existing accounts by `manage.py backfill_free_package` (`seed_packages` runs it too) and
   idempotent (one active baseline per user). It is a record: it never expires, cannot be
   cancelled and is ignored when resolving a purchased/granted package, which replaces the
   default for its duration.
2. The user's live `UserPackage` (active, not expired, not the baseline): it replaces the
   default.
3. The newest unexpired `PackageQuotaOverride` of that user package (staff exception; empty
   fields inherit, 0 = unlimited).
4. The account's `UserQuotaOverride` (**Shop → Account overrides**): per account, whatever
   package it holds. Files and data volume (`limit_files`, `limit_size` + unit) per window
   (optionally a different `window`), item-type switches (`covers_free`, `covers_featured`,
   `covers_paid`), `device_limit`, `expires_at`, a required `reason` and `granted_by`. Empty
   fields inherit, 0 = unlimited, one active override per account. **Precedence: account
   override > package override > package > free default.** Grant, change, revoke and delete
   are written to the append-only `QuotaOverrideAudit` by `apps/shop/overrides.py`.
5. Modifiers registered with `register_entitlement_modifier(fn)` (campaigns, see
   `docs/04-backend/promotions.md`); use `entitlements.grant(...)` so bonuses are
   consistent and explained in `sources`.

Fields: `download_quota` (files per `window`, `None` = unlimited), `window`, `unlimited`,
`device_limit` (`None` = unlimited), `can_access_featured`, `can_upload_paid` (approved
sellers), `item_types`, `paid_discount_percent`, `perks`, `limits` (resolved caps and the
metered item types), `window_start`/`resets_at`, `package_name`, `plan_summary`, `sources`,
`package`, `tier`, `slot_reset_credits`, `featured_blog`, `featured_blog_limit`. Results are memoised per request by `EntitlementCacheMiddleware`.

Everything goes through it: `active_package`, `quota_snapshot`, `quota_windows`,
`check_quota`, `consume_quota`, `price_for`, `request_purchase`, `entitlement_summary`.

## Contract used by other apps

* `apps.firmwares.access.check_download_access(user, firmware)` asks `has_purchased`,
  `effective_entitlements(...).can_access_featured`, `check_quota` and the package's
  `max_concurrent`. A file already served to the user is always re-downloadable (and never
  charged again). `finalize_served_download` calls `consume_quota(user, firmware, reason=<tier>)`.
* Per item type: **free** files are counted in the bucket only when the package covers free
  files; **featured** files need a package that covers them and share its quota; **paid**
  files need a purchase for that exact file (a file bought individually is always allowed),
  are counted when the package covers paid files, and are never blocked by a quota.
* `has_purchased`, `active_package`, `quota_snapshot`, `consume_quota` (idempotent per
  user + file), `request_purchase`, `purchase_url`, `credits_url`, `file_links`,
  `EntitlementError` (`QuotaExceeded`, `NotEntitled`, `PurchaseError`).
* `services.activate_package(user, package, *, reference, order, source, duration_days)` ends
  the previous live package (renewing the same package stacks the period) and
  `services.mark_order_paid(order_id, staff_user=..., reference=...)` provisions an order
  exactly once. `apps.devices` reads `effective_entitlements(user).device_limit`.
* `apps.shop.api.create_credit_topup_order(user, *, credits, price, reference)` opens an
  offline order for a wallet top-up.

## Orders, credits and events

Cash orders are offline: users request a package or a paid file, staff confirm payment in
the Unfold admin (mark paid / cancel / refund dialogs from `apps/shop/dialogs.py`).
Online payments are disabled (`services.ONLINE_PAYMENTS_ENABLED = False`).

**Pay with credits** (needs `apps.wallet`; otherwise credits are shown as unavailable and
every cash flow keeps working): `apps/shop/credits.py` spends through
`apps.wallet.api.spend` with the sinks declared in `apps/shop/spend_sinks.py`
(`package.purchase`, `file.purchase`) and idempotency key
`shop:<kind>:<user>:<object>:<n>`, then records an order paid by credits and fulfils it
instantly. A package costs its derived `credit_price` (or its promotional credit price); a
paid or featured file costs the credits of its USD price (`to_credits(price_for(...))`).
The package's paid-file discount applies to paid files only. A staff refund of such an order calls `wallet.refund`.
The confirm dialog is `templates/shop/dialogs/pay_with_credits.html` (a partial for HTMX, a
full page without JavaScript); not enough credits links to the wallet "Ways to earn" page.
The wallet seam is `apps/shop/wallet_gateway.py`.

### One featured or paid file with credits

An account **without** a package that covers featured files can unlock a single file with
credits (`credits.pay_file_with_credits`, dialog `shop:file_credits`):

* paid file: its staff-approved USD price less the package's paid-file discount;
* featured file: `ShopSettings.featured_file_price` (USD, **Shop → Shop settings**, 0 = not
  sold one by one; firmware rows may only carry `approved_price` when they are paid).
* credits = `to_credits(price)`; the spend, the order and the per-file entitlement commit in
  one transaction, so a failure after the charge rolls the charge back. Buying again is
  refused ("already own"), a package holder with featured access is refused too
  (`credits.purchase_block_reason`): the package quota is used and nobody pays twice.
* the entitlement is per file and lifetime: `has_purchased` lets `check_quota` allow it even
  when the free allowance is exhausted, and `consume_quota` records it as
  `QuotaConsumption.Reason.PURCHASED` (served, listed in **My files**, never counted in the
  free or fair-usage quota). `apps/firmwares/access.py::_featured_decision` accepts it.

### Featured blog articles (contract for `apps.blog`)

`apps/shop/api.py`: `featured_blog_access(user, post_id, price_usd) -> BlogAccess(allowed,
via, credit_price, reason)`, `purchase_featured_blog(user, post_id, price_usd, key)`,
`featured_blog_buyers(post_id)`, `purchased_featured_blog_ids(user)`. Access comes from an
earlier purchase (`FeaturedBlogPurchase`, lifetime), an earlier package unlock
(`FeaturedBlogUnlock`, counted once per article) or a package with
`applies_to_featured_blog` and room in its `featured_blog_limit` per window (the first read
unlocks and counts it). Purchases spend `to_credits(price_usd)` on the wallet sink
`blog.featured_purchase`, are atomic and idempotent on `key`, and never charge a package
holder whose package covers the article.

After commit, `mark_order_paid` / `refund_order` publish `shop.order_paid` /
`shop.order_refunded` on the event bus: `{"order_id", "user_id", "topup_credits", "files":
[{"content_type": "app_label.model", "object_id", "amount", "credits"}]}` (paid-file lines
only; `credits` is 0 for cash orders).

## Admin

`PackageTier` has ONE Unfold form with tabs (Basic, Bandwidth, Item types, Extra, Fair usage,
Perks, Preview), the summary in the list and the preview tab, filters (active / public /
default), activate/deactivate actions, row and detail dialogs to duplicate (inactive, hidden
draft) and archive (hides the package, cancels its pending orders, holders keep it) and a
Holders inline. `UserPackage` carries the quota-override inline; `Order` shows its items
inline with mark-paid, cancel and refund dialogs.
