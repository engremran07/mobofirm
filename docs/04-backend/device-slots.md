# Device slots

A device slot is one approved browser profile for an account. `apps.devices` owns slot
state and resets; it never decides how many slots an account has.

## Where the limit comes from

`apps.shop.entitlements.effective_entitlements(user).device_limit` is the only
authority. It combines the free package defaults, the active package, per-user
`PackageQuotaOverride` rows and any registered entitlement modifiers (campaigns).
`DeviceConfig` and `DEVICE_MAX_DEVICES_PER_USER` only seed the free package's default;
they are not a second source of truth. Browser identity still uses the random
first-party browser key; only its keyed digest is stored.

## Freeing a slot

The price of a paid reset is per account: `apps/devices/credits.py::price(user)` resolves the user's
effective entitlements → `PackageTier.slot_reset_credits` (empty = none) → the global
`device.slot_reset` price staff edit under Credits economy → Credit prices (default **10**;
`sync_registry` never overwrites a row staff already priced). Price modifiers (campaigns) apply
to either. The device dialog, the slot options and **Ways to spend** show that price for the
signed-in user and the ledger debits exactly it. Seeds: Free package 20, Premium and Gold empty
(= global).

`apps/devices/slots.py` is the single service. `slot_options(user)` resolves what the
user can do right now (`SlotOptions`: `at_limit`, `cap_reached`, `resets_left`,
`can_pay`, `missing_credits`, the rewarded offer). There are three ways to free the
slots, all audited as a `DeviceSlotReset` row plus device events:

| Method | Function | Counts toward the monthly cap |
|--------|----------|-------------------------------|
| Staff | `reset_slots_as_staff` | No |
| Credits | `reset_slots_with_credits` (spend sink `device.slot_reset`) | Yes |
| Rewarded ad | `grant_reset_allowance` then `reset_slots_with_allowance` | Yes |

A user who cannot or will not use these can ask staff (`request_staff_reset`), which
notifies staff and records a `DeviceSlotRequest`.

Rules:

- Every reset is idempotent per caller-supplied key; a replayed key returns the
  recorded reset, and another account's key is refused.
- The user row is locked for the whole operation, so two resets cannot race.
- A reset releases the approved browsers (state change) and never deletes audit
  history.
- Self-service resets are capped per calendar month by
  `DeviceConfig.slot_reset_monthly_cap`.

## Pages and dialogs

`templates/devices/includes/slot_options.html` renders the options on the device page.
The confirmations are app-owned dialogs (`apps/devices/dialogs.py`) built on the core
dialog shell: `slots/ask-staff/`, `slots/pay-credits/` and `slots/use-rewarded-reset/`.
Each also works as a plain page without JavaScript.

## Rewarded-ad resets

Only offered while an enabled `RewardedAdConfig` of reward type `device_reset` exists;
otherwise the page says it is not available. See [rewarded-ads.md](rewarded-ads.md).

## Tests

`tests/backend/devices/` covers limits from entitlements, each reset method, caps,
idempotency, ownership checks and audit rows.
