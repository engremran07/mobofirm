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

- A Brand with Models cannot be deleted: `Model.brand` protects the catalog rows from
  cascading deletion. Reassign or explicitly remove its Models before deleting the Brand.
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

## Existing catalog correction proposals

- Verified members can suggest descriptive corrections from the brand, chipset, model and
  flashing-tool detail pages. The proposal form is at
  `/firmwares/catalog-updates/<target-kind>/<id>/`; members can track outcomes at
  `/firmwares/catalog-updates/mine/`.
- Proposals are owned by `firmwares.CatalogUpdateProposal`. A pending proposal snapshots
  only the fields it changes and the corresponding current values. One member can have one
  pending proposal per catalog item; identical retries return the existing proposal, while
  a different pending submission is rejected. New submissions are limited to ten per
  member per hour.
- Only descriptive fields are allowed: brand descriptions, chipset-vendor aliases, chipset
  family/description, non-identity model specifications, tool description/version, and
  guide-template title/summary. Names, slugs, manufacturer codes, relationships, URLs,
  active/featured state, safety level, tool instructions, generated posts, and firmware
  records are not editable by these proposals. Published flashing-guide article corrections
  use the blog-owned update-proposal flow instead.
- Staff review uses the existing `change_<model>` permission for the specific target.
  Approval may correct the proposed values, writes through the target model's normal save
  method, and records the reviewer, time, reason, and admin history entry. Rejection needs a
  reason. If any proposed source field changed while waiting, review marks the proposal
  stale and never overwrites that newer catalog data. Outcome notifications are dispatched
  after commit.
- `firmwares.0017_catalogupdateproposal` creates only the proposal table and its target and
  pending-duplicate constraints; it does not rewrite existing catalog rows.

## Upload page

`/firmwares/contribute/` (formerly `/firmwares/upload/`, which redirects with 301; a POST to the
old address is still handled) requires brand and model (cascading) and a variant **only when the model
has at least one active variant** (see `catalog-variants.md`; models without variants show
"No specific variant" and proceed), then a stored file or an
allowlisted HTTPS link. The result is a `PendingFirmware` linked to that exact variant (or to
the model when it has none);
staff approval publishes it under the model and variant and refreshes the automated blog
posts. The page always shows an **Access control** section. Every contributor may choose
**Free** or **Featured**; **Paid** is shown disabled for everyone else, with a note ("Only
approved sellers can offer paid files") that links to the marketplace become-seller page when
that optional app is installed and enabled. A forged Paid value from a non-seller is ignored
in the form and refused by `resolve_submission_tier` / `submit_user_upload`. Approved marketplace
sellers (`apps.marketplace.services.is_active_seller`) additionally get Paid with a required
`proposed_price` above zero; only staff set `approved_price`. When a reviewer approves a file
at a lower access type than requested (`approve_pending_firmware(access_tier=...)`) a reason
(`notes`) is required and the contributor's approval notification names the change.
Contributions are tracked under **My contributions › Firmware contributions**
(`/contributions/firmware/`).

## Chipset scope and firmware formats by chipset

The catalogue covers **smartphone and tablet SoCs only**. `apps/firmwares/chipsets.py`
(`SUPPORTED_CHIPSET_VENDORS`) is the allow-list: Google Tensor, MediaTek (Helio, Dimensity,
MTxxxx), Qualcomm Snapdragon, Samsung Exynos, Unisoc / Spreadtrum (RDA Microelectronics is
a Unisoc-owned label), Rockchip, Allwinner (A133, A523, T507, ...), HiSilicon Kirin and
Apple (A-series for iPhone and iPad, M-series for iPad; not Mac). Chipset text of any other
vendor (for example Intel) resolves to no `Chipset` row. Old rows of other vendors are
reported by `manage.py prune_unsupported_chipsets` (dry run by default; `--apply` deletes only
rows no model, variant, firmware, tool, request or automated post references).

Each `Chipset` points to one `ChipsetVendor` record; vendor names and aliases are therefore
canonical rather than repeated free text. `Spreadtrum`, `SPRD`, and RDA labels resolve to
Unisoc, while the original chipset spellings remain searchable aliases. The vendor
normalization migration refuses to proceed if canonicalization would merge two chipset rows
with the same name; those collisions require an explicit data decision first. Reversing the
migration restores canonical vendor names, not the pre-migration spelling of each row.

`apps/firmwares/package_formats.py` is the single source of truth for `package_format`: the
format depends on the chipset vendor of the selected model (the variant's chipset first, then
the model's, then free chipset text; Samsung-brand devices are always Odin, Apple-brand devices
always Apple). It also maps each format to its flashing tool (seeded `FlashingTool` slug).

| Chipset vendor | Vendor-specific formats (stored value) | Flashing tool |
| --- | --- | --- |
| MediaTek | Scatter firmware (`scatter_firmware`: `*_Android_scatter.txt` plus images), download agent package (`mtk_download_agent`) | SP Flash Tool |
| Qualcomm | QFIL package (`qfil_package`: rawprogram/patch XML plus firehose programmer, EDL 9008), fastboot ROM | QPST and QFIL, Platform-Tools |
| Samsung Exynos | Odin package (`odin_package`: AP/BL/CP/CSC `.tar.md5`) | Odin |
| Unisoc / Spreadtrum / RDA | PAC package (`pac_package`) | SPD ResearchDownload and Upgrade Tool |
| Rockchip | `update.img` package (`rockchip_update_img`) | RKDevTool |
| Allwinner | PhoenixSuit / LiveSuit `.img` (`allwinner_img`) | PhoenixSuit |
| HiSilicon Kirin | `UPDATE.APP` package (`kirin_update_app`) | Huawei HiSuite (eRecovery) |
| Google Tensor | Factory image (fastboot zip), full OTA | Platform-Tools, Android Flash Tool |
| Apple | IPSW restore image (`apple_ipsw`), Apple OTA / delta (`apple_ota`) | Finder / Apple Devices app, Apple Configurator 2 |

Generic formats are valid for every chipset: recovery ROM, fastboot ROM, full OTA,
incremental OTA, factory image, partition image and other; "Not specified" and
**Service / Engineering package** (`service_package`) are always accepted. A device whose
chipset is unknown accepts every format. A format of another vendor (scatter for a Qualcomm
model, IPSW for an Android model) is rejected by `PendingFirmware.clean`,
`BaseFirmware.clean`, the upload form, the upload/external-submission services and the
approval service. The upload page narrows the **Package format** select to the chosen
model's formats: `firmwares:upload_variant_field` returns the variant field plus the select
as an HTMX out-of-band swap (`#upload-format-region`). Firmware detail pages show the format
and a link to its flashing tool; the browse page and admin lists filter by format; automated
blog posts name the vendor's package and guide steps from the same module.

Apple devices are restored with IPSW images that Apple still signs. The catalogue seeds
Finder / Apple Devices app, iTunes, Apple Configurator 2, idevicerestore and futurerestore
with that limit stated, and Apple guides (restore, recovery mode, DFU mode per generation,
Configurator revive, OTA versus IPSW, signed-only downgrade). **Activation Lock and iCloud
bypass are out of scope**: no tool or guide for them is catalogued or generated.

**Service / Engineering firmware** is the user-visible name of the `engineering`
classification (`EngineeringFirmware`) and of `service_package`; database tables, the
`type=engineering` filter and URL slugs are unchanged.

## Tool and guide categories

`FlashingToolCategory` keeps the legacy hand-made `general` categories (a tool's own
`category` FK) and adds generated kinds, created and kept in sync by
`apps/firmwares/tool_categories.py` (idempotent):

| Kind | One per | Source fields | Slug |
| --- | --- | --- | --- |
| `chipset_vendor` | vendor of the phone/tablet allow-list (Allwinner, Apple, Google Tensor, HiSilicon, MediaTek, Qualcomm, Samsung Exynos, Unisoc, Rockchip) | `vendor` | `chipsets-<vendor>` |
| `chipset_family` | product family under its vendor (MediaTek → Helio, Dimensity, Kompanio; Qualcomm → Snapdragon; Samsung → Exynos; HiSilicon → Kirin; Unisoc → Unisoc T, Spreadtrum, RDA; Google → Tensor; Apple → A-series, M-series) | `vendor`, `family`, `parent` | `chipsets-<vendor>-<family>` |
| `brand` | `Brand` (created on `post_save`, rename-safe through `slug_history`, deactivated, never deleted, when the brand is deleted; a recreated brand reuses its category) | `brand` | `brand-<brand slug>` |
| `os_type` | supported device OS (Android, iOS / iPadOS) | `os_key` | `os-<key>` |

Membership is **derived**, never stored twice (`CategoryIndex`): a tool belongs to the vendor
and family categories of its chipsets, the brand categories of `supported_brands`, the OS
category (iOS when it supports Apple, else Android) and its own legacy category; a guide belongs
to its brand, to the vendor/family of its model's chipset (or chipset text) and to its OS.
Staff edits to a generated category's name, description, icon and order survive later syncs.

Run on an existing database (after `migrate`): `manage.py seed_tool_categories --confirm`
(categories only) or `manage.py seed_flashing_tools --confirm` (categories plus the missing
tools). `seed_chipsets` and `seed_catalog` also sync them. Nothing is published.

**Public display rule.** A category is listed on `/firmwares/tools/` ("Browse by chipset,
brand and system"), on the guides filter and in `tool-categories` sitemap only when it has at
least one active tool or published guide, or a child that does. `/firmwares/tools/category/<slug>/`
returns 404 for an empty category, redirects an empty family to its listed parent and an
old brand slug to the current one (301). Empty categories stay in the staff console with kind,
parent, auto badge, tool and guide counts and an "empty" flag, and filters for kind, parent,
source, active and content.

## Official OEM tools

`seed_data/tools.py` lists only publisher tools (`tool_type=OEM`) plus clearly labelled
community tools (Odin, SP Flash Tool, ResearchDownload, RKDevTool, PhoenixSuit) with a risk
note; there are no mirrors, cracked or patched tools and no Activation Lock or FRP bypass tools.
Where an official download page could not be verified (Lenovo Rescue and Smart Assistant,
Xperia Companion, ASUS Flash Tool, Smart Switch, Apple's Finder / Apple Devices app) the tool
has the publisher's support page as `official_url` and **no `download_url`**. Oppo, Realme,
Nothing, HMD and Transsion publish no verified public flashing tool, so none is listed.

## Import and export (staff console)

`apps/core/admin_import_export.py` (`ImportExportMixin`, `Resource`, re-exported by
`apps/core/admin_base.py`) adds Import and Export to the Brands, Models, Chipsets, Firmware
(official), Flashing tools, Tool categories and Guide templates changelists. No third-party
package: CSV and JSON always, XLSX only when `openpyxl` is installed. Resources live in
`apps/firmwares/admin_io.py`; each documents its columns on the page.

- **Export** (`view` permission): scope is the current changelist filters, the selected rows
  (bulk actions *Export selected rows as CSV/JSON*) or every row. CSV/JSON are streamed; text
  cells starting with `= + - @`, tab or carriage return get a leading `'` (the importer strips
  it again).
- **Import** (`add` and/or `change`): upload → dry-run preview (create / update / unchanged
  plus per-row errors, nothing written) → confirm → one atomic apply with a `LogEntry`. Apply
  re-plans inside the transaction and refuses any file with an error. At most 5000 rows and
  2 MB. Rows are matched by natural key, so importing the same file again is a no-op.
- **Natural keys**: brand slug; model brand + slug (or brand + model code); chipset slug
  (derived through `normalize_chipset`, other vendors rejected); tool slug; category slug;
  guide template title + type + brand + chipset filter; firmware download URL.
- **Firmware** rows are external links validated with `validate_external_url` and the package
  format of the model's chipset, and are created through `submit_external_firmware` as
  `PendingFirmware`. They never land verified, never change published firmware and need the
  `add_pendingfirmware` permission. Guide templates cannot enable auto-generation and tools
  cannot be `crack`.
- Bulk writes hold back the automated blog refresh (`signal_handlers.batched_blog_sync`); each
  brand, model and chipset post refreshes once after the commit.

## Download gate

`apps.firmwares.access.check_download_access(user, firmware) -> AccessDecision` is called by
the token issue, start, local serve and external redirect. Paid files need
`apps.shop.entitlements.has_purchased`; featured files need an active package
(`effective_entitlements(user).can_access_featured`); quota is checked with the shop and
consumed once per file on first serve (`consume_quota`). Every served file is recorded once in
`ServedDownload`.

### Integrity rules (F-039, F-040)

- **Identity.** A download token belongs to a signed-in user or to a signed `HttpOnly` cookie
  (`mf_dl`, `download_identity.py`; only a keyed hash is stored in `DownloadToken.anon_hash`).
  The IP never identifies a token; it only feeds the anonymous daily quota and a per-IP limit of
  40 new anonymous tokens an hour. The ad-gate, start and serve steps refuse anyone but the owner.
- **Signed link.** `/firmwares/serve/<id>/?token&exp&sig`: the signature covers the token, the
  firmware, the principal (`u<pk>` or `a<hash>`) and the expiry. The admin TTL is an upper bound
  clamped to 300 s. The link opens at most `REDEMPTION_BUDGET` (5) times (resume and ranged
  requests); only the **first** opening charges the file and counts as served. Anything else
  (another browser, after the budget, after the transfer ended or the lease expired) is a 404.
- **Concurrent downloads are leases** (`download_leases.py`, owned by the access responsibility).
  A `DownloadSession` holds a transfer slot from admission (token consumed, for the link TTL)
  until the transfer is reported finished (`report_transfer`; the Django streaming path in
  `DEBUG` reports exactly, a reverse proxy's completion log feed may call the same function)
  or `lease_expires_at` passes: link TTL before the file is opened, a size-aware window
  (size / 256 KiB/s, 10 min to 6 h) after. Expiry is evaluated when counting, so a lost
  completion signal never holds a slot; `expire_download_tokens` only tidies the rows.
  Admission runs under a lock on the user row, so parallel starts cannot all pass an N-slot cap.
  External hosts are a hand-off: no slot, never counted. `bytes_delivered` stays 0 until a
  transfer reports; nothing claims bytes before they are sent.
- **Ad gate.** The client sends nothing but the token and page signature. The server fixes the
  kind of ad, and `AdGateLog.proof` records the evidence: `dwell` (the server's own timer, a delay
  and attention signal, **not** an impression) or `ad_unlock` (the user holds a live rewarded-ad
  unlock of this file, proven by the ads app, so the sponsor step is skipped). A server-side
  proof for everyone else needs an ads reward type for the gate (open request to `ads`).

### Typed firmware references (F-019)

Firmware lives in six tables with no shared key, so other tables store a typed reference
(catalogue key + UUID). `references.py` is the one place that builds and reads it and
`firmware_cleanup.py` the one place that cleans it:
`content_type` + `object_id` columns for tokens, served downloads, views, attempts, stats and
the access history, `firmware_type` + `firmware_id` for the link shortener. Deleting a firmware
runs `firmware_cleanup.purge_firmware_references`: tokens (with sessions and gate logs), views, attempts, stats,
library rows and shortener rows go; pending access-change requests are withdrawn; the
append-only access history and shortener audit rows stay, carrying the file name. Access
history rows protect their content type from deletion. A single `Firmware` table
(`kind` column) remains the end-state; it is a data migration of six tables and their eight
referrers and needs an explicit decision before it is started.

### Module ownership (F-023)

Every module of `apps/firmwares` belongs to one responsibility; imports across responsibilities
follow a declared matrix that `tests/backend/firmwares/test_module_ownership.py` enforces:

| Responsibility | Modules |
|---|---|
| base | `models`, `tracking_models`, `access_change_models`, `shortener_models`, `references`, `firmware_cleanup`, `constants`, `conf`, `sources`, `crypto`, `variant_rules`, `package_formats`, `chipsets`, `shortener_constants`, `notifications` |
| catalogue | `catalog`, `catalog_lookup`, `chipset_audit`, `seeding`, `seed_verification`, `sitemaps`, `search_providers`, `seo_providers`, `popularity` |
| access | `access`, `access_changes`, `access_requests`, `auto_feature`, `package_history`, `dialogs`, `admin_access`, `download_leases` |
| intake | `services`, `submissions`, `forms`, `views`, `serializers`, `catalog_requests`, `catalog_review`, `ai_analysis`, `reward_events`, `events`, `user_views`, `admin_requests`, `admin_io` |
| serving | `download_service`, `download_identity`, `public_views`, `api` |
| guides | `guide_service`, `tool_matcher`, `tool_categories`, `views_flashing` |
| automation | `blog_automation`, `signal_handlers`, `tasks` |
| shortener | `shortener`, `shortener_providers`, `shortener_views`, `admin_shortener`, `urls_short` |
| wiring | `admin`, `urls`, `apps` |

`base` imports nothing else (two lazy catalogue lookups are the listed exceptions), `access`
never imports `serving` or `intake`, and the catalogue core never imports downloads or intake.

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
