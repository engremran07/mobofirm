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
| `TimestampedModel`, `SoftDeleteModel`, `AuditFieldsModel` (abstract model bases) | `core.models` | ads, comments, firmwares, seo, tags |
| `effective_entitlements(user)`, `register_entitlement_modifier` | `shop.entitlements` | firmwares, devices, users, ads, promotions |
| `grant_reward`, `spend`, `refund`, escrow, `register_reward_event`, `register_spend_sink` | `wallet.api` | shop, firmwares, bounty, gamification, referral, marketplace, ads, users |
| `has_consent(request, category)` | `consent.services` | ads, site_settings |
| `check_download_access`, `record_served_download` | `firmwares.access` | shop, users (My contributions) |
| `is_active_seller`, `seller_for` | `marketplace.services` | firmwares, shop, promotions |
| `is_active_seller` (trust rule for featured/paid posts), via `AppService.get("marketplace")` | `marketplace.api` | blog |
| `featured_blog_access`, `purchase_featured_blog` (+ optional `featured_blog_buyers`, `purchased_featured_blog_ids`), via `AppService.get("shop")` | `shop.api` | blog |
| `bounty_summary`, `availability` (guide requests), via `AppService.get("bounty")` | `bounty.api` | blog |
| `blog.guide_fulfilled`, `blog.guide_request_closed`, `blog.post_published` (event bus) | `blog.guide_requests`, `blog.rewards` | bounty (settle/refund), gamification |
| `is_profile_complete`, `missing_profile_fields` | `users.services.profile_completion` | marketplace |
| `slot_options`, slot reset services | `devices.slots` | ads (rewarded resets) |
| Public firmware queryset | `firmwares.catalog` | pages, marketplace, storage, shop, users |
| SafeFetcher | `seo.fetcher` | distribution, firmwares (the third-party download link shortener adapters, `firmwares.shortener_providers`; same SSRF-safe path, no second HTTP client) |
| `shorten_download_url(firmware, *, request=None)` (link to share for an external download; first-party `/d/<code>/` and/or provider short link per the staff setting, with automatic fallback), via `AppService.export("firmwares", ...)` | `firmwares.api` | distribution share text (optional, may call it later) |
| Console admin kit: `ModelAdmin` bases, filters, dialog/action forms (`admin_base`, `admin_filters`, `admin_forms`, `admin_actions`) | `core.admin_*` | every app with a staff admin |
| `internal_request` (per-process crawl identity; suppresses view counters and other visitor side effects for the in-process SEO crawler) | `core.internal_request` | seo, ads, referral, marketplace, distribution, blog, firmwares |
| `register_ads_lines` (publisher lines served at `/ads.txt`; `apps.seo` never imports `apps.ads`) | `seo.wellknown` | ads |
| `rewarded_offer("download_unlock")` (is an unlock ad configured, and its page URL) | `ads.api` via `AppService.export` | shop |
| `register_reward_handler(reward_type, RewardHandler)`; the shop's file-unlock handler | `ads.rewarded`, plugged by `ads.unlock_bridge` | ads (bridge -> `shop.ad_unlock`) |
| `has_ad_unlock(user, firmware)` (an unused unlock ticket for the file) | `shop.entitlements`, read by `firmwares.access` through the optional module seam | firmwares (gate, shortener) |
| Event bus (`shop.order_paid`, `contribution.approved`, ...) | `core.events` | marketplace, referral, gamification, bounty, promotions |
| `search_queryset`, `search_querysets`, `search_items`, `list_search_context`, `did_you_mean`, `popular_entries`, `search` (unified engine; apps contribute `search_providers.py`) | `core.search` | firmwares, blog, tags, marketplace, wallet, users, pages |
| Search providers (`search_providers.py`) | each app, registered with `core.search` | core search |
| `RichTextFormMixin`, `RichTextField`, `RichTextAdminMixin`, `sanitize_rich_text`, `sanitize_stored_html`, `render_rich_text`, `video_embed_options` (rich-text editor library; profile overrides plug in with `register_profile_source`) | `core.editor` | blog, comments, pages, marketplace, promotions, users, site_settings |

## Known exceptions in the baseline

These existed before the rule and are listed so they stay visible:

- `blog` and `comments`/`tags` import each other's `models` and `selectors` (shared
  editorial content).
- `blog` dispatches `ads.tasks` and `tags.tasks` directly instead of by task name.
- `core` imports `models` of `ads`, `blog`, `comments`, `devices`, `firmwares` and
  `users` for the cleanup command, the admin dashboard and the seed orchestrator.
- `firmwares` imports `core.models.TimestampedModel` for its timestamped catalog and
  automation records; the abstract base owns no firmware-domain state.
- `users->devices`, `users->firmwares`, `users->wallet` for the account pages and the
  Device and My contributions views. `users->firmwares` includes `access` (the Downloads
  tab reads `served_statuses`, the one place that applies the download
  lifetime rules).

Reducing these (events or contracts instead of model imports) is welcome and never
needs a baseline change.
