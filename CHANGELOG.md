# Changelog

Notable project changes are recorded here. Versions follow Semantic Versioning, and release notes follow Keep a Changelog.

## [Unreleased]

### Added

- Add data-driven ad placement: a fixed zone vocabulary rendered by one `{% ad_zone %}` tag in the shared public layouts, the staff placement manager and scan-suggestion queue in the admin shell, a staff-only test profile (signed short-lived preview links, no counting), the `seed_ad_test_profile` command, a consent-gated `/ads/go/` click redirect and `docs/04-backend/ads-placement.md`.
- Add the idempotent `seed_packages` command (Free, Premium and Gold packages) and call it, together with two draft launch promotions, from `seed_platform`.
- Add a cross-app import ratchet (`scripts/app_imports.py`, `tests/backend/test_app_boundaries.py`) and `docs/04-backend/app-dependencies.md`, `device-slots.md` and `rewarded-ads.md`.
- Add featured and paid blog posts (`Post.access`, staff-approved USD `price`): readers without access get a teaser with `isAccessibleForFree` paywall markup, a Buy-with-credits dialog and no leakage in feeds, search or the sitemap; the shop is reached only through a lazy gateway. Documented in `docs/04-backend/blog-featured-and-guides.md`.
- Add staff-gated guide requests (`blog.GuideRequest`): members request a flashing method, pattern, trick or guide; staff approve in a review queue before it is public; approved requests accept credit bounties (new bounty kind `guide`) and hunter solutions that state free, featured or paid access, and publishing a solution pays the escrow and the blog reward events.
- Add the distribution growth engine (`docs/04-backend/distribution.md`): a top-level Distribution admin group (dashboard with channel health, circuit breaker, queue depth, clicks and Run queue now / Test connection / Send test post / Preview; content calendar; approval queue with edit-before-send; link analytics with CSV export and best-hour heatmap); sixteen platform adapters behind one registry (webhook, Telegram, Discord, Slack, X, Mastodon, Bluesky, Facebook, Instagram, Threads, LinkedIn, Reddit, Pinterest, TikTok, YouTube Shorts, WhatsApp Cloud API) with per-platform credential forms; a hook-first post composer with smart hashtags, UTM and first-party `/go/<code>/` click counting (bots filtered, no personal data), optional referral links, A/B opening-style winners, best-time windows, quiet hours, per-day caps, evergreen re-shares and launch boosts; Pillow share cards and ffmpeg vertical videos (reported as "needs media" when the tools are missing); campaign presets and retention settings.
- Add `apps/referral/api.py` (`referral_code_for`) so other apps can attach a referral code through `AppService`.
- Add the SEO content scan, review and fix workflow: `seo_scan` and **Run scan now** audit every public page, post, firmware file, model, brand, chipset, tool and tag in-process (no HTTP, no Celery; time-bounded, resumable, idempotent, auto-resolving) against explicit title, description, canonical, structured-data, URL and content standards; the issues board groups findings by object type, proposes standards-based fixes from each object's own data, applies them through the SEO override layer with a ledgered before/after and rollback (single, bulk "safe suggestions", ignore with reason, re-audit), respects the SEO autonomy mode, and **Process queue now** drains the SEO job queue without Celery. Documented in `docs/04-backend/seo-platform.md`.
- Add SEO interlinking review (relation-backed brand, model, chipset, tool, guide and post link proposals with budgets, anchor rotation and accept, reject and bulk decisions), flash-guide coverage per model and chipset with synchronous refresh through the firmware automation, an in-process render crawl with measured link counts, and `{% related_links %}` / `apps.seo.api.link_body_html` (external links get `nofollow`) for templates. All SEO staff screens now render in the admin shell.

- Add the economy pricing rules: every price is US dollars and credits are always `price × WalletSettings.credits_per_currency` through the one `apps.wallet.api.to_credits` (packages, paid files, featured files, featured blogs, promotions); `PackageTier.credit_price` is derived with an optional labelled override, the wallet settings page is now **Credits & pricing** (rate warning, ledger never rewritten) and the package editor shows a live `$ · credits` readout.
- Add the device slot-reset price tier: `device.slot_reset` defaults to 10 credits, staff edits apply immediately everywhere, `PackageTier.slot_reset_credits` overrides it per package (Free seed 20) and `apps.devices.credits.price(user)` resolves user → effective entitlements → package override → global price (modifiers still apply), shown to each account on the device dialog and **Ways to spend**.
- Add the shop side of featured blogs (`apps/shop/api.py`: `featured_blog_access`, `purchase_featured_blog`, `featured_blog_buyers`, `purchased_featured_blog_ids`): `FeaturedBlogPurchase`, the package `featured_blog` item type with a per-window article quota (`FeaturedBlogUnlock`), idempotent atomic purchases through the `blog.featured_purchase` sink and no double charge for package holders.
- Add the free package for every account: a `UserPackage` baseline row is created at sign-up (allauth e-mail and social, admin, commands), `backfill_free_package` (also run by `seed_packages`) assigns it to existing accounts, and the baseline never expires, cancels or replaces a purchased package.
- Add account-level quota overrides (`UserQuotaOverride`, **Shop → Account overrides**): files and data volume per window, item types, device limit, expiry and reason, one active per account, precedence account override > package > free default, with an append-only `QuotaOverrideAudit`.
- Add buying a single featured or paid file with credits for accounts without a qualifying package (`shop.ShopSettings.featured_file_price`, USD × rate): atomic, rolled back on failure, lifetime per file, never counted in the free allowance and shown in **My files**; package holders use their package instead.
- Render the staff device-events page in the admin shell.

### Fixed

- Fix the shop tests, seed and promotions after `credit_price` became derived, and derive promotional credit prices from the discounted dollar price.
- Store ad-scan template paths with forward slashes (backslash paths on Windows never matched template names), make approving two scan suggestions of one template zone link one placement instead of failing, and list only non-removed placements on the ads dashboard.
- Split a multi-file order's credit reward proportionally across its lines instead of crediting it once per file.
- Gate analytics on consent in the site-settings tests, list rewarded-ad grants in the admin sidebar, and keep device-slot configuration superuser-only.

## [0.2.0] - 2026-11-07

### Added

- Establish a changelog-driven Semantic Versioning and tested GitHub release workflow.
- Add one runtime version source (`app/version.py`: `pyproject.toml` version plus git SHA/build date from `APP_GIT_SHA`/`APP_BUILD_DATE`), shown in the public footer, `<meta name="application-version">`, the admin header, sidebar and dashboard, `/health/` and the `X-App-Version` header, with `scripts/release.py` bump/tag/changelog flow documented in `docs/08-operations/releases.md` and tests that fail when surfaces disagree.
- Add an always-visible light/dark/system theme switcher to the admin header (persisted, follows the OS live, adopts the public `mobofirm-theme` choice, no inline scripts).
- Add a "Related" tab on the model admin page (firmware by category, pending uploads, automated blog post), an "Activity" tab on the user admin page (devices, packages, orders, wallet, seller profile, uploads, comments, notifications) and a "Catalog requests" dashboard KPI.
- Add production deployment assets: multi-stage `Dockerfile`, `docker-compose.yml` (web, worker, beat, PostgreSQL, Redis, nginx), nginx and gunicorn configuration, `.dockerignore`, database backup/restore scripts, `scripts/dev_up.ps1`, and `docs/08-operations/deployment.md`.
- Add `ensure_system_user` and `bootstrap_site` management commands, a Celery beat retention schedule, API token expiry/rotation, and a `public_display_name` user property that never exposes an email address.
- Install the correlation-ID, maintenance-mode, password-reset-throttle and profile-completion middleware, and enforce device registration quotas, expiry and strict/MFA locking modes.
- Install and integrate `storage`, `seo`, `pages`, `shop`, `marketplace` and the central `site_settings` app (runtime branding, contact, email, reCAPTCHA, download gate, analytics and verification files in one cached accessor).
- Add the Autonomous SEO Control Center in `apps.seo` (one metadata resolver, SafeFetcher, crawler and audit, sitemap/robots/IndexNow, change ledger with rollback), the `Chipset` model with chipset and brand pages, external-download firmware, and automatic per-model, brand and chipset blog posts.
- Replace the Django admin skin with `django-unfold`: grouped sidebar, KPI dashboard, filters, actions and a test that loads every registered model.
- Add the `seed_platform` command (22 brands, 131 models, 262 firmware listings, tools, guides, articles, comments) and `scripts/reset_dev_db.ps1`.
- Poll the email-verification pages every five seconds so they continue on their own once the address is verified.
- Add strict mypy (django-stubs) alongside Ruff.
- Install `wallet`, the central credits economy: append-only ledger with running balances, admin-editable reward rules (caps, cooldowns, anti-abuse, campaign modifiers) and spend prices, escrow holds, offline staff-confirmed top-ups, staff-reviewed payouts, a wallet dashboard (`/wallet/`) and the `apps.wallet.api` contract other apps use to earn and spend credits. No payment gateway is involved.
- Install `referral`: personal link/code, invited-user dashboard (`/referral/`), and a reward paid through the wallet only after the invitee verified their email and had a first contribution approved, with self-referral, same-network, same-device and cap checks; sign-up requests without a session are tolerated.
- Install `consent` as the single consent source: versioned policy, anonymised audit records, a CSP-safe server-rendered banner and the privacy centre (`/consent/`), `has_consent(request, category)`, footer "Cookie settings", and a retention purge. Google Analytics/Tag Manager (loader and CSP hosts) and ad measurement now load only after the matching category is allowed. The old `CONSENT_BANNER_ENABLED` setting, the `users` consent models and the unused `AdsSettings` consent fields are removed.
- Install `gamification` (XP rules, levels, badges, streaks, live leaderboard) driven by approved firmware, fulfilled requests, approved comments, verified referrals and fulfilled bounties, with `/community/badges/`, `/leaderboard/`, `/community/progress/` and a `user_badges` template tag.
- Install `bounty`: credit bounties on firmware requests, models and variants, escrowed in the wallet and paid to the uploader when staff approve a matching upload; refunds on cancel/expiry, claims, staff dispute queue and audit log; pages under `/bounty/`.
- Install `distribution`: opt-in sharing of newly approved firmware and new blog posts to webhook, Telegram, Discord and X channels through the SEO SafeFetcher, with per-channel templates, idempotent jobs, retries, terminal failures, a dashboard and an audit log. Video generation and paid SDKs were dropped.

### Removed

- Remove the personal file-storage app (`filemanager`: folders, user uploads, share links, trash and versions) in code, templates, scripts, settings, admin and tests. "My files" (`/files/`) now lives in `users` and shows only files served to the account, its uploads and its quota.
- Make `/firmwares/api/upload/` JSON-only; browsers that open it are sent to the upload page.
- Remove the `apps.governance` Django app; moderation is now app-owned (firmware, blog, comments and tags).

### Changed

- Production settings now validate secure cookies, HTTPS origins, shared Redis cache, mail backend, encryption keys and proxy topology at startup; `/health/ready/` also checks the cache and returns only `ok`/`unavailable`.
- Country detection uses a trusted edge header instead of sending visitor IPs to a third-party service.

### Fixed

- Fix `MAILERS` options so production email sending works on Django 6.1, and give CI a test-only secret key.
- Fix device behavior insights (they were never recorded) and cumulative device risk scores.
- Fix stacked cards overlapping the footer (the firmware detail page) and the interests checkbox group overflowing on the profile page.
- Fix `select_for_update` on a nullable join that crashed comment notifications on PostgreSQL.
- Fix the consent banner linking to a privacy-policy page that is not published, and the admin theme choice (stored under `adminTheme`) not persisting or following the OS.

### Security

- Resolve client IPs without trusting `X-Real-IP` or client-supplied `X-Forwarded-For`; make rate limits atomic and per client address.
- Restrict DRF default authentication to sessions, remove Basic authentication, and require explicit expiring token authentication; apply MFA attempt limits and device policy to the API and Google sign-in paths.
- Close or redirect the stock allauth login, signup and Google token routes, require reCAPTCHA tokens when enabled, and fix open redirects, MFA disable and inactive-user checks.
- Harden ad creative validation, ad event trust, and the ads settings toggle.

[Unreleased]: https://github.com/engremran07/mobofirm/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/engremran07/mobofirm/releases/tag/v0.2.0
