# Device slots

A device slot is one approved **registered device** for an account: one operating system
(a platform WebAuthn credential) or, as a fallback, one browser profile. `apps.devices`
owns slot state and resets; it never decides how many slots an account has.

## Device identity: the operating system, not the browser

A website cannot read an OS install id, a hardware serial or a TPM key directly. The
web-only way to bind a registration to the *operating system* is a **platform
authenticator credential** (Windows Hello/TPM, macOS or Android keystore): it is created
by the OS, shared by every browser running in that OS user profile, and destroyed when the
OS is reinstalled. `apps/devices/os_identity.py` (service) and `apps/devices/webauthn.py`
(verification, built on the already-installed `cryptography`) implement it; no dependency
was added.

**Registration** (`POST devices/os/options/` then `devices/os/register/`): the dialog's
"Register this computer" runs `navigator.credentials.create()` with
`authenticatorAttachment: "platform"`, `attestation: "none"`, ES256/RS256, and
`excludeCredentials` for the account's existing credentials (so one OS cannot take two
slots). The client posts the credential id, `clientDataJSON`, `authenticatorData` and the
SPKI public key (`getPublicKey()`), so no CBOR parsing is needed. The server checks the
single-use challenge, `type`, `origin`, `rpIdHash`, the user-present flag and that the
credential id matches, then stores only public material on `Device`
(`os_credential_id`, `os_credential_id_hash` unique per account, `os_credential_public_key`,
`os_credential_alg`, `os_credential_sign_count`, `os_credential_rp_id`). Registering is
idempotent per (account, credential), approves the OS immediately when the allowance has
room (otherwise it stays pending and the user is told), and upgrades the browser's own
fallback row in place so it keeps its slot.

**Recognising another browser** (`devices/os/options/` then `devices/os/verify/`): on a
browser the account has not been seen in, the dialog shows "Verify this computer".
`navigator.credentials.get()` signs a fresh server challenge with the OS credential; the
server verifies challenge/origin/rpId/UP flag, the ES256 or RS256 signature against the
stored key and a monotonic signature counter (when non-zero). Success creates a
`DeviceBrowserLink` from that browser's cookie to the OS `Device`, folds the browser's
placeholder (pending row deleted; an approved fallback row hands its slot to the OS), and
every later sign-in in that browser resolves to the same `Device`: **same OS = same
Device = one slot, whichever browser**. A link is honoured for
`DEVICE_OS_LINK_MAX_AGE_DAYS` (30) after its last verification, then the browser verifies
again, which bounds a browser profile restored onto a reinstalled OS.

Challenges live in the session, are bound to account and purpose, expire after
`DEVICE_WEBAUTHN_CHALLENGE_TTL` (300 s) and are consumed even by a failed attempt.
Allowed origins: the request's own origin (host already validated by `ALLOWED_HOSTS`, so
`http://localhost:8000` works), `SITE_URL`, and `DEVICE_WEBAUTHN_ALLOWED_ORIGINS`.
`DEVICE_WEBAUTHN_RP_ID` overrides the relying-party id (default: the request host).
`DEVICE_OS_IDENTITY_ENABLED=False` turns the feature off. Endpoints are POST-only, behind
the global CSRF middleware, require a verified email and are rate limited per account.
Each public service function is one `transaction.atomic` block under the account row lock;
refusals are audited after the rollback (`os_register_*` / `os_verify_*` device events).

**Fallback**: the random first-party cookie remains the identity only where no platform
authenticator is usable (unsupported browser, non-secure context, user cancelled, no
screen lock/Hello configured). The UI and admin label the two kinds "This computer" and
"This browser only". An account that registered its OS is never asked to register again
in another browser; it is only asked to verify.

### Honest limits

- A website cannot prove an OS install or hardware: this is a credential held by the OS
  keystore. **Reinstalling the OS destroys it**, so the reinstalled computer cannot
  authenticate as the old device and is treated as unregistered. The server cannot *see*
  the reinstall, so the old device keeps its slot until staff/credit/ad slot reset,
  revocation, or the `device_expiry_days` trust expiry frees it.
- Apple and Google passkeys may sync across the user's own devices (iCloud Keychain,
  Google Password Manager); on such setups one credential can be present on several
  devices, which then count as one. This cannot be detected from a website.
- A browser that blocks WebAuthn, an insecure origin (WebAuthn needs HTTPS or
  `localhost`; a bare IP such as `127.0.0.1` is not a valid relying-party id), or a
  computer without a configured platform authenticator falls back to per-browser
  registration.
- Browsers on the same OS share the credential on Windows (Windows Hello) and Android;
  on macOS a browser may keep its own keychain-scoped credential, so a second browser
  there may need its own registration.
- `attestation: "none"` means the authenticator model is not verified; the credential
  proves continuity of one OS keystore, not hardware authenticity.
- A credential is bound to its relying-party id: a registration made on one host name
  is not offered on another.

## Where the limit comes from

`apps.shop.entitlements.effective_entitlements(user).device_limit` is the only
authority. It combines the free package defaults, the active package, per-user
`PackageQuotaOverride` rows and any registered entitlement modifiers (campaigns).
`DeviceConfig` and `DEVICE_MAX_DEVICES_PER_USER` only seed the free package's default;
they are not a second source of truth. The fallback browser identity uses the random
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
idempotency, ownership checks and audit rows. `test_os_identity.py` covers OS
registration, browser switching, one-OS-one-slot, and every verification rejection
(bad signature, replayed/expired/wrong-purpose challenge, wrong origin or rpId, another
account's credential, revoked/blocked device, full allowance, signature counter) with a
software platform authenticator (`webauthn_support.py`); `test_device_login_flow.py`
drives the real sign-in view (pending notification wording, approval, browser switch).
`tests/frontend/test_device_os_identity_browser.py` runs the real module in Chromium with
a CDP virtual platform authenticator.
