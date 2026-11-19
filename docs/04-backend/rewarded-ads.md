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
| `download_unlock` | counts one watch toward a shop **ad unlock ticket** (see below); no wallet, no grant row |

### Download unlock (`download_unlock`)

The reward type is generic: `apps.ads` knows nothing about tickets. Owning apps plug a
`RewardHandler(check, grant)` into `rewarded.register_reward_handler(reward_type, ...)`; the
shop's is registered by `apps/ads/unlock_bridge.py` at startup (the same seam as the
entitlement modifier), and a `download_unlock` config reports "File unlocks are not
available on this site." until it exists.

* `RewardedAdView.reference` stores what a watch counts toward (`unlock:<ticket id>`). The
  rewarded page takes it from `?ref=` and keeps it in the session per config, so the
  start call needs no extra client code; `handler.check` runs in `check_eligibility`
  (refusing a missing, foreign, closed or capped ticket with stable error codes) and
  `handler.grant(view)` runs inside the completion transaction with the view locked.
* The shop counts each view at most once (`AdUnlockProgress.view_id` is unique), so a
  replayed or parallel complete can never advance a ticket twice. Everything else the
  existing flow guards still applies: verified email, advertising consent, per-user start
  rate limit, per-config caps, one active view per user, flagged abuse rows.
* Staff create one enabled config of this type (`seed_packages` creates a disabled one,
  named "Download unlock"): pick its placement, set the watch time and leave the per-config
  cooldown and caps at 0, because the **package** decides the cooldown between ads and the
  daily cap (Package builder → Ads & unlocks).

Rules, states and the ticket lifecycle: `docs/04-backend/entitlements.md` ("Unlock by
watching ads").

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

`RewardedAdConfig` and `RewardedAdView` are in the staff console. The ads dashboard
shows a clear "not configured" state, abuse flags and completion analytics.

## Tests

`tests/backend/ads/` covers missing consent, replay, too-fast completion, caps and each
reward type; browser tests cover the page flow with a shortened watch time.
