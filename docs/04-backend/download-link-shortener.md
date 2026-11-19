# External download link shortener

**Owner: `firmwares`.** The feature wraps the firmware download gate (token, countdown, optional
ad interstitial), the external-URL allow-list (`FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS`) and the
firmware Fernet key (`apps/firmwares/crypto.py`), all owned there. It needs one new import layer,
`firmwares -> seo.fetcher` (the SSRF-safe HTTP client; no second HTTP stack), listed in
`app-dependencies.md` and the boundary baseline. Other apps use only the contract.

## What is shared

Only the **download page** of an approved, active, external firmware is shortened. The file-host
URL is never shared, never sent to a provider, and the final redirect after the gate is unchanged.

| Mode | Shared link |
| --- | --- |
| Off (default) | the detail page, as before |
| First-party | `https://site/d/<code>/` |
| Third-party | provider short link of the download page URL |
| Both | provider short link of the first-party link |

Overrides, strongest first: per-firmware (`FirmwareShortenerOverride`: type + id), per-brand
(`Brand.shortener_mode`, inherit by default), global (`ShortenerSettings.mode`). Hosted files are
never shortened.

**Who sees shortened links (the audience gate).** `shortener.should_shorten(user, firmware)` is the
single hook: anonymous visitors, the free tier and any package without the `direct_links` perk are
shortened; a package with `direct_links` (Silver, Gold, Platinum) gets the plain gated download
link. `ad_free` alone does not exempt anyone. The perk is read through the existing shop
entitlement contract (`access.has_perk` -> `apps.shop.entitlements.effective_entitlements`, optional
and resolved at call time, no static import); without the shop everybody counts as the free tier.
The check uses the viewer of the page (`request.user`, or the `user=` argument of the contract),
so a Silver member's own "Copy link" is the plain link. Per-package or rewarded-ad unlock rules
belong inside `should_shorten` (it already receives the firmware) and nowhere else; callers never
re-check the perk.

Surfaces: the share widget (Copy link and share buttons) of the firmware detail and download pages,
and `AppService.export("firmwares", "shorten_download_url")(firmware, request=None) -> str`
(always shareable: it falls back to the plain download page URL). Distribution may call it later;
generated blog posts keep their in-site relative links so they stay idempotent.

## First-party links

`DownloadShortLink(code, firmware_type, firmware_id, target_digest)`; idempotency key
`(type, id, sha256(external_url))`. Codes use `secrets`, 31 symbols x 8, a collision retries up to
5 times inside a savepoint and then falls back to the plain link (audited, nothing persisted).
`/d/<code>/` (GET/HEAD) refuses a malformed, unknown, tampered, withdrawn or stale code (404): the
destination is derived from the live firmware and must still match the digest. It answers `302` to
`firmwares:firmware_download` only, with `no-store` and `noindex`. Clicks: one
`DownloadShortLinkDay` counter row per link and day; the bot policy is a copy of
`distribution.shortlinks` (no import).

The first-party link earns indirectly through the ad interstitial page of the gate and gives
attribution (clicks per day).

## Third-party providers

`shortener_providers.py`: `isgd`, `tinyurl` (no key), `query_api` (monetized `?api=KEY&url=`
services; endpoint template with `{key}` and `{url}`), `json_api` (GET or POST, auth header, JSON
body template, dotted response path). Only https answers are accepted. Timeouts 1-10 s, no redirect
following, 64 KB cap. A failure is reduced to a stable code (the library message may quote the URL,
so it is dropped); keys are never logged, shown or stored in clear.

Boundaries: no HTTP call happens inside a transaction this module opens. Cache row: race-safe
`get_or_create` under `sha256(kind, endpoint, method, epoch, target)`; editing provider settings
bumps the epoch and resets the circuit. Breaker: 5 consecutive failures pause the provider for 15
minutes, then one probe decides. Audit: `ShortenerOutcome` (created, failed, circuit opened, health
check), newest 2000 rows kept, stable codes and hosts only.

Honest caution (also shown in the console): shorteners that pay per visitor usually pay little, add
a step for the visitor, and their links are often flagged by Safe Browsing and ad networks.

## Console

`Downloads & testing` group: Link shortener settings (singleton, *Test provider* button), Short
download links (daily clicks), Provider short links, Shortener log, Shortener overrides. The
per-brand choice is the *Link shortening* tab of the brand page.

## Migrations

`firmwares/0008_download_link_shortener` (additive: five new tables, one defaulted `Brand` column).
