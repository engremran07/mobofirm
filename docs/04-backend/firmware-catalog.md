# Firmware catalogue: requests, uploads, access and events

Owner: `apps.firmwares` (code, templates under `templates/firmwares/`, tests under
`tests/backend/firmwares/` and `tests/backend/integrations/`).

## Catalogue creation rules

| Object | Who creates it | Required parents | Optional |
| --- | --- | --- | --- |
| Brand | Staff in the admin, or end users through a **brand request** | none | logo, website |
| Model | Staff (Add model) or **model request** | **brand** | chipset |
| Variant | Staff (Add variant) or **variant request** | **brand and model** (the model must belong to the brand); a request also needs one identifying field (code, region/market, RAM / storage or carrier) | chipset text, region, carrier, model code |
| Chipset | Staff or **chipset request** | none | family, aliases |

- End-user pages: `/firmwares/requests/` (hub), `/requests/brand|model|variant|chipset|firmware/`
  and `/requests/mine/`. Signed-in, email-verified users only; CSRF; redirect after POST.
  Each form shows live "does it already exist?" hints (`/requests/check/`) and refuses an
  existing or already pending entry. Volume is limited per user and hour
  (`FIRMWARE_REQUESTS_PER_HOUR`).
- Brand, model and variant are picked with the shared combobox
  (`templates/components/combobox.html`) fed by the central search
  (`/search/suggest/?kinds=brand|model|variant|chipset`, `filter.brand`, `filter.model`).
- Admin request queues ("… requests (review)") are decision queues, not add forms (no add
  permission). Pending requests stay editable until decided. **Approve** creates (or links)
  the real record from the requested data in one transaction, after an app-owned confirm
  dialog where the reviewer may correct name, slug and the like; **Reject** needs a reason.
  Both exist as row actions, detail-page actions and bulk changelist actions (a confirm page
  with a note or reason). Every decision runs through `apps.firmwares.catalog_review`
  (re-exported by `catalog_requests`, the same services as the REST viewsets): row lock,
  idempotent (a decided request cannot be decided again), reviewer and timestamp stored, an
  admin history (`LogEntry`) entry for the request and for a created record, the normal save
  hooks of admin-created records (blog automation), and a notification to the requester
  after commit. Approving a model request uses the requested brand and chipset; approving a
  variant request creates the variant under the requested model (code normalised, RAM and
  storage read from the request); approving a **firmware request** accepts it (status *in
  progress*, supporters notified) and creates the requested variant when the model lacks it.
  The dashboard **Catalog requests** KPI and the sidebar badges count the pending requests of
  the queues the staff member may view, link to a queue that has work, and refresh as soon as
  a request is filed or decided.
- Admin **Add variant** has a brand select that loads only that brand's models over HTMX
  (`admin:firmwares_variant_model_options`); the form also validates server-side that the model
  belongs to the brand.
- Empty or not-found states include `templates/components/request_cta.html` (covered for the
  brand, chipset, model, firmware, variant and central search pages by
  `tests/backend/firmwares/test_request_cta_empty_searches.py`).
- Every request form field has an example placeholder and help text (Brand `Xiaomi`, Model
  `Redmi Note 13 Pro 5G`, model code `2312DRA50G`, variant `AI2401_A`, chipset
  `MediaTek Dimensity 9200 (MT6985)`, RAM / storage `8 GB / 256 GB`, region `Global`); keep
  them when adding fields.
- There is no external catalogue sync, scraper or auto-fill: the admin fields are entered by
  hand or come from approved requests and uploads.

## Upload page

`/firmwares/upload/` requires brand and model (cascading) and a variant **only when the model
has at least one active variant** (see `catalog-variants.md`; models without variants show
"No specific variant" and proceed), then a stored file or an
allowlisted HTTPS link. The result is a `PendingFirmware` linked to that exact variant (or to
the model when it has none);
staff approval publishes it under the model and variant and refreshes the automated blog
posts. The page always shows an **Access control** section. Normal users' uploads are always
free: the tier select shows Free selected with Featured and Paid present but disabled, a note
("Only approved sellers can offer paid or featured files") links to the marketplace
become-seller page when that optional app is installed and enabled, and the server ignores any
forged `access_tier`. Approved marketplace sellers
(`apps.marketplace.services.is_active_seller`) get the live tier select and may propose `paid`
or `featured`; the amount (`proposed_price`, above zero) appears only for Paid; only staff set
`approved_price`.

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
