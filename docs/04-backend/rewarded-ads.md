# Rewarded ads

`apps.ads` owns the rewarded-ad flow. It is disabled until staff configure it, and
every user-facing surface says so honestly when it is off.

## What must be true before a view can start

1. Ads and rewarded ads are enabled in the ads settings.
2. A `RewardedAdConfig` is enabled, not deleted, and has a ready placement.
3. The reward backend exists on this site (credits need `apps.wallet`; device resets
   need `apps.devices`).
4. The user is signed in with a verified email.
5. The visitor has given advertising consent
   (`apps.consent.services.has_consent(request, "advertising")`).
6. Per-user caps allow it: cooldown, daily and monthly limits.

`rewarded.unavailable_reason(config)` returns the user-facing reason when any of these
fails; `check_eligibility` raises `RewardedAdError` with a stable code.

## Reward types

`RewardedAdConfig.RewardType`:

| Type | Effect |
|------|--------|
| `credits` | `grant_reward("ads.rewarded_view_completed", ...)` through the wallet reward rule |
| `device_reset` | grants one reset allowance, used by `apps.devices.slots` |
| `download_bonus` | extra downloads through the entitlement modifier seam |
| `ad_free` | a timed ad-free period |

## The flow

`apps/ads/rewarded.py`, served by `rewarded_views.py`:

- `start` creates a `RewardedAdView` and returns a signed single-use token. Only the
  token's digest is stored. One active view per user.
- `heartbeat` accrues watch time from server timestamps. The client's claimed duration
  is never trusted; a gap longer than `HEARTBEAT_MAX_GAP_SECONDS` stops accrual.
- `complete` succeeds only once, and only after the server-measured watch time reaches
  the minimum (`effective_min_watch`). A client claiming more time than the server saw
  beyond a small tolerance is flagged. Completing twice is a no-op.

Abuse flags recorded on the view: `completed_too_early`, `impossible_duration`,
`replayed_token`, `too_frequent`, `abandoned_restart`. Repeated early completions and
too many starts or rejected rows per hour are refused.

## Endpoints

`/ads/rewarded/<config_id>/` (page), `/ads/rewarded/<config_id>/start/`,
`/ads/rewarded/heartbeat/`, `/ads/rewarded/complete/`.

## Provider-agnostic

There is no third-party SDK. Completion proof is the server-controlled timer plus the
site's own creative rendering, so the CSP is unchanged.

## Admin

`RewardedAdConfig` and `RewardedAdView` are in the Unfold admin. The ads dashboard
shows a clear "not configured" state, abuse flags and completion analytics.

## Tests

`tests/backend/ads/` covers missing consent, replay, too-fast completion, caps and each
reward type; browser tests cover the page flow with a shortened watch time.
