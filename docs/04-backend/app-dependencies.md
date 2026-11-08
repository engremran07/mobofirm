# App dependencies and boundaries

Every installed app is self-contained: it owns its models, services, admin, forms,
dialogs, templates (`templates/<app>/`) and tests. Apps reach each other only through
documented contracts, signals or the event bus.

## Rules (enforced by `tests/backend/test_app_boundaries.py`)

1. An app never imports another app's `admin`, `views`, `urls` or `forms`.
2. `dialogs` may only be imported from `core`, which provides the generic dialog shell
   (`apps/core/dialogs.py`, `templates/components/dialog.html`). The content of every
   dialog lives in the app that owns the behaviour.
3. `core` is shared infrastructure. It never imports `bounty`, `consent`,
   `distribution`, `gamification`, `marketplace`, `pages`, `promotions`, `referral`,
   `seo`, `shop`, `storage` or `wallet`; it reaches them through events and contracts.
4. Every other cross-app import must already be in the measured baseline in
   `tests/backend/app_boundaries_baseline.py`. A new dependency fails the test until
   it is justified and added there and to the table below.

The baseline is a ratchet: removing an edge needs no change; adding one is a deliberate
decision. `scripts/app_imports.py` prints the current graph as JSON.

## Contracts between apps

| Contract | Owner | Used by |
|----------|-------|---------|
| `effective_entitlements(user)`, `register_entitlement_modifier` | `shop.entitlements` | firmwares, devices, users, ads, promotions |
| `grant_reward`, `spend`, `refund`, escrow, `register_reward_event`, `register_spend_sink` | `wallet.api` | shop, firmwares, bounty, gamification, referral, marketplace, ads, users |
| `has_consent(request, category)` | `consent.services` | ads, site_settings |
| `check_download_access`, `record_served_download` | `firmwares.access` | shop, users (My files) |
| `is_active_seller`, `seller_for` | `marketplace.services` | firmwares, shop, promotions |
| `is_active_seller` (trust rule for featured/paid posts), via `AppService.get("marketplace")` | `marketplace.api` | blog |
| `featured_blog_access`, `purchase_featured_blog` (+ optional `featured_blog_buyers`, `purchased_featured_blog_ids`), via `AppService.get("shop")` | `shop.api` | blog |
| `bounty_summary`, `availability` (guide requests), via `AppService.get("bounty")` | `bounty.api` | blog |
| `blog.guide_fulfilled`, `blog.guide_request_closed`, `blog.post_published` (event bus) | `blog.guide_requests`, `blog.rewards` | bounty (settle/refund), gamification |
| `is_profile_complete`, `missing_profile_fields` | `users.services.profile_completion` | marketplace |
| `slot_options`, slot reset services | `devices.slots` | ads (rewarded resets) |
| Public firmware queryset | `firmwares.catalog` | pages, marketplace, storage, shop, users |
| SafeFetcher | `seo.fetcher` | distribution |
| Event bus (`shop.order_paid`, `contribution.approved`, ...) | `core.events` | marketplace, referral, gamification, bounty, promotions |
| Search providers (`search_providers.py`) | each app, registered with `core.search` | core search |

## Known exceptions in the baseline

These existed before the rule and are listed so they stay visible:

- `blog` and `comments`/`tags` import each other's `models` and `selectors` (shared
  editorial content).
- `blog` dispatches `ads.tasks` and `tags.tasks` directly instead of by task name.
- `core` imports `models` of `ads`, `blog`, `comments`, `devices`, `firmwares` and
  `users` for the cleanup command, the admin dashboard and the seed orchestrator.
- `users->devices`, `users->firmwares`, `users->wallet` for the account pages and the
  Device and My files views.

Reducing these (events or contracts instead of model imports) is welcome and never
needs a baseline change.
