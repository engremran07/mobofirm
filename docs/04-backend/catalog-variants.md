# Catalogue variants: decision record

Owner: `apps.firmwares` (`variant_rules.py`, `Variant`, `VariantCreationRequest`,
`forms.UploadForm`, `templates/firmwares/fragments/upload_variant.html`).

## Problem

A device model can ship in several hardware or regional variants, written as codes such as
`AI2401`, `AI2401_A` and `AI2401_D`. Many brands and models have no variants at all. Forcing
every upload to name a variant made uploads for those models impossible, and free-text
variants produced near-duplicate codes (`ai2401_a`, `AI2401 A`).

## Options considered

1. **Always require a variant, add a placeholder "Default" variant to every model.** Simple
   validation, but it pollutes the catalogue, creates variant pages with no meaning and
   makes the "is a variant missing?" prompt useless.
2. **Variant is always optional.** Easy uploads, but files for a model that clearly has
   variants end up unattributed, which defeats per-variant flashing guides and download
   pages.
3. **Variant is required exactly when the model has active variants (chosen).** The data
   decides: uploaders of a model without variants are never blocked, uploaders of a model
   with variants must say which one, and a missing variant is requested rather than guessed.

## Decision

- **Optional unless needed.** A variant is required for an upload only when the chosen model
  has at least one *active* variant (`variant_rules.variant_is_required`). Deactivating all
  variants of a model makes the field optional again. Otherwise the upload is stored against
  the model with no variant ("No specific variant"), and approval publishes it under the
  model. The rule is enforced in `UploadForm._validate_device` and again in
  `submissions.submit_user_upload`, so every caller is covered.
- **Live form.** When the brand or model changes, `uploadForm.onPick` asks the server for the
  variant field (`firmwares:upload_variant_field`, an HTMX fragment) and the field becomes
  a required picker (model with variants), a read-only "No specific variant" (model without
  variants), or a disabled hint (no model yet). The no-JavaScript fallback is the server
  rendering of the same fragment on every page load. The refresh is coalesced (one request per
  pick), keeps an already chosen variant that still belongs to the model, and removes the
  combobox `<noscript>` fallback after the swap (HTMX parses fragments with scripting off, so
  that fallback would otherwise become a second, empty `variant` field).
- **Variant requests** need the brand and the model (the model must belong to the brand) and
  at least one identifying field: the variant **code**, the **region or market**, **RAM /
  storage** or the **carrier**. The name is optional; when empty it is built from the
  identifying fields (code first). The name alone does not identify a variant.
- **Codes.** `normalize_variant_code` trims, joins inner whitespace with `_` and upper-cases
  (`" ai2401 a "` becomes `AI2401_A`; hyphens are kept: `SM-S911B`). `Variant.save` and
  `Variant.clean` normalise, and the database enforces
  `variant_model_code_unique_ci`: unique per model ignoring case, for non-empty codes only.
  A pending request cannot repeat a pending code of the same model
  (`variant_request_pending_code_unique`), and a request for an existing code is refused
  with a pointer to the existing variant.
- **RAM and storage.** The request keeps the text the user typed (`8 GB / 256 GB`).
  Approval reads it (first figure RAM, second storage, GB; TB converted) into
  `Variant.ram_options` and `storage_options`; unreadable text stays only in the request.
- **Carrier.** New `Variant.carrier` for operator-locked builds.
- **Transitioning model-level codes.** `Model.variant_codes` is currently an additive,
  normalized projection of the legacy model code fields and related `Variant.model_code`
  values. Catalogue display and automated model-post content read this list. The `Variant`
  relation remains in place and continues to own regional, memory, carrier, and board
  identity until its dependent records and workflows are migrated; this field alone does
  not merge or split model rows.
- **Code ownership across active models.** `Model.clean()` rejects a normalized code
  already claimed by another active model of the same brand, checking both the stored
  model-code projection and legacy variant rows. Inactive models and models from other
  brands do not block reuse. Model request approval, catalogue imports, and seeding call
  model validation before writing. The transitional `Variant` table remains until its
  callers are migrated.
- **Model slugs.** When a model is saved without a slug, its first normalized model code
  supplies the slug; if it has no code, the model name does. A same-brand collision gets
  the first available numeric suffix, so same-name models can coexist without discarding
  their chipset or code identity.
- **Chipset-scoped model requests.** A model request for a known chipset is compared with
  existing and pending records for that same chipset, allowing a same-name model on another
  chipset. Requests without a chipset retain the prior conservative duplicate behavior.
  Approval links only to a same-chipset model when one was specified; a new primary code
  supplies the model slug when available.

## Migration

`firmwares/0005_variant_identity` is additive and forward compatible: new blank-default
columns, then a data step that normalises stored codes and, for duplicates, keeps the oldest
and suffixes later ones `_DUP<n>` (nothing is deleted; staff fix them), then the
constraints. The seed catalogue contains no duplicate codes.

`0015_model_variant_codes` adds a normalized model-level projection and backfills it in
batches from `Model.model_code`, `Model.model_codes`, and related variant codes. It does not
remove or rewrite any of those source values; reversing the migration drops only the
derived projection.

`0016_remove_modelcreationrequest_model_request_pending_name_unique_and_more` scopes pending
model-request uniqueness to `(brand, chipset, case-insensitive name)`, with a separate
constraint for requests where chipset is unknown. Existing records are not rewritten.

## Verification

`tests/backend/firmwares/test_variant_rules.py` (rules, constraint, admin form, requests),
`tests/backend/firmwares/test_upload_page.py` (optional variant, HTMX fragment, approval
without variant) and `tests/frontend/test_catalog_requests_browser.py` (live variant field).
