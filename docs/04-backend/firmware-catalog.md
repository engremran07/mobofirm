# Firmware catalogue: requests, uploads, access and events

Owner: `apps.firmwares` (code, templates under `templates/firmwares/`, tests under
`tests/backend/firmwares/` and `tests/backend/integrations/`).

## Catalogue creation rules

| Object | Who creates it | Required parents | Optional |
| --- | --- | --- | --- |
| Brand | Staff in the admin, or end users through a **brand request** | none | logo, website |
| Model | Staff (Add model) or **model request** | **brand** | chipset |
| Variant | Staff (Add variant) or **variant request** | **brand and model** (the model must belong to the brand) | chipset text, region, model code |
| Chipset | Staff or **chipset request** | none | family, aliases |

- End-user pages: `/firmwares/requests/` (hub), `/requests/brand|model|variant|chipset|firmware/`
  and `/requests/mine/`. Signed-in, email-verified users only; CSRF; redirect after POST.
  Each form shows live "does it already exist?" hints (`/requests/check/`) and refuses an
  existing or already pending entry. Volume is limited per user and hour
  (`FIRMWARE_REQUESTS_PER_HOUR`).
- Brand, model and variant are picked with the shared combobox
  (`templates/components/combobox.html`) fed by the central search
  (`/search/suggest/?kinds=brand|model|variant|chipset`, `filter.brand`, `filter.model`).
- Admin request queues ("… requests (review)") are review-only: no add, no edit. Approve and
  reject run through `apps.firmwares.catalog_requests` (the same services as the REST
  viewsets) and notify the requester. Approving a model request uses the requested brand and
  chipset; approving a variant request creates the variant under the requested model.
- Admin **Add variant** has a brand select that loads only that brand's models over HTMX
  (`admin:firmwares_variant_model_options`); the form also validates server-side that the model
  belongs to the brand.
- Empty or not-found states include `templates/components/request_cta.html`.
- There is no external catalogue sync, scraper or auto-fill: the admin fields are entered by
  hand or come from approved requests and uploads.

## Upload page

`/firmwares/upload/` requires brand, model and variant (cascading), then a stored file or an
allowlisted HTTPS link. The result is a `PendingFirmware` linked to that exact variant;
staff approval publishes it under the model and variant and refreshes the automated blog
posts. Normal users' uploads are always free. Approved marketplace sellers
(`apps.marketplace.services.is_active_seller`) may also propose `paid` or `featured` with a
`proposed_price`; only staff set `approved_price`.

## Download gate

`apps.firmwares.access.check_download_access(user, firmware) -> AccessDecision` is called by
the token issue, start, local serve and external redirect. Paid files need
`apps.shop.entitlements.has_purchased`; featured files need an active package
(`effective_entitlements(user).can_access_featured`); quota is checked with the shop and
consumed once per file on first serve (`consume_quota`). Every served file is recorded once in
`ServedDownload`.

## Events and rewards

Approval of an upload (`approve_pending_firmware`) runs after commit:

1. `firmware.approved` is published on `apps.core.events.event_bus`. Payload (stable):
   `firmware`, `firmware_id`, `firmware_type`, `pending`, `pending_id`, `uploader`,
   `reviewer`, `brand_id`, `model_id`, `variant_id`, `access_tier`,
   `fulfilled_request_ids`.
2. Open firmware requests for the same model and firmware type (a request naming a variant
   only matches that variant) are closed as fulfilled in the approval transaction.
3. When the wallet app is installed, `apps.wallet.api.grant_reward` pays the uploader.
   Event keys (registered in `apps/firmwares/reward_events.py`; amounts and caps are the
   admin-editable wallet `RewardRule`s): `firmware.approved` (free),
   `firmware.approved.paid`, `firmware.approved.featured` and
   `firmware.request_fulfilled` (once per fulfilled request). The idempotency key is
   `firmware.approved:<pending id>` / `firmware.request_fulfilled:<request id>`. Reviewers
   never earn for their own uploads.

Right after it, `contribution.approved` with `{"user_id": <uploader id>, "kind": "firmware"}`
is published for the referral rewards (not published for uploads without an uploader).

Subscribers (bounty, gamification, distribution, referral) must not raise; the bus logs and isolates
handler failures.
