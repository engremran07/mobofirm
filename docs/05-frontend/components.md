# Shared UI components

Reusable Django template partials live in `templates/components/` (and `templates/includes/` for the site shell). They are the single implementation of each pattern; app templates include them instead of copying markup. Load `{% load mf_ui %}` (from `apps/core/templatetags/mf_ui.py`) only where the helper filters or tags below are used directly.

All components use Bootstrap classes plus the Mobofirm design tokens (`frontend/src/styles/tokens.css`). None of them emit inline scripts or styles, so they work under the strict CSP.

## Include contracts

| Partial | Parameters | Notes |
| --- | --- | --- |
| `components/page_header.html` | `title` (required), `eyebrow`, `lead`, `title_id` | Renders the page `<h1>`; use once per page. |
| `components/breadcrumbs.html` | `current` (required), `parent_label`, `parent_url`, `grandparent_label`, `grandparent_url` | Home is implicit; the last item carries `aria-current="page"`. |
| `components/pagination.html` | `page_obj` (required), `label`, `hx_target` | Links use `{% querystring page=N %}`, so search, filter and sort parameters survive. With `hx_target` each link also has `hx-get`, `hx-target` and `hx-push-url`, and stays a plain link without JavaScript. |
| `components/empty_state.html` | `title` (required), `message`, `action_label` + `action_url` | `role="status"`. |
| `components/form_field.html` | `field` (required BoundField), `help` | Label, widget, an always-present `[data-field-error]` container (`<id>_errors`, `role="alert"`, hidden while empty) and help text (`<id>_help`); the widget gets `aria-describedby` (help + errors) and `aria-invalid` from `mf_widget`. Checkboxes render as `form-check`. Hidden fields render bare. `account/_field.html` is the floating-label variant with the same container and, for new-password inputs, the live checklist. |
| `components/form_errors.html` | `form` | Non-field errors as `role="alert"`. |
| `components/stat_tile.html` | `label`, `value` (required), `hint` | Static tile; it never lifts on hover. |
| `components/search_box.html` | `action`, `input_id`, `label` (required), `value`, `name` (default `q`), `placeholder`, `hx_target`, `hx_get` | A GET form that works without JavaScript. With `hx_target` the input itself debounces (`input changed delay:350ms`), shows an indicator and pushes the URL. Never use `from:input`, which binds to the first input in the document. |
| `components/spinner.html` | `id` (required), `label` | An `hx-indicator` target. |
| `components/dialog.html` | `dialog_id`, `dialog_title` (required), `dialog_message`, `dialog_size`, `danger`, `dialog_action`, `confirm_label`; blocks `dialog_body`, `dialog_footer` | The generic dialog shell (native `<dialog>` + Alpine `appDialog`). Apps extend it in `templates/<app>/dialogs/` and serve it with `apps.core.dialogs.DialogView` into `#dialog-slot`. See `dialogs.md`. |
| `components/confirm_dialog.html` | `dialog_id`, `dialog_title`, `dialog_message`, `dialog_action`, `confirm_label` (required), `hidden_name`, `hidden_value`, `danger`, `reason_label`, `reason_required`, `confirm_phrase` | The standard confirm: an app-owned `<dialog>` with a CSRF-protected POST form, extending `dialog.html`. Open it with `<button type="button" data-dialog-open="<dialog_id>">`. Esc and the close button close it; a backdrop click closes it unless `danger`. The server still validates the POST. Never use native `confirm()` or the HTMX confirm attribute; use `data-confirm-dialog` or `Dialogs.confirm` instead. |
| `includes/site_search.html` | `search_id` (unique per page) | Header search: a `components/combobox.html` in `navigate` mode fed by the central search engine, inside a plain GET form to `search:results`. |
| `components/combobox.html` | see "Combobox contract" below | The single type-ahead picker (ARIA 1.2 editable combobox) for header search, country/calling-code and catalog pickers. |
| `includes/nav_links.html` | none | Primary links; `nav_current` (from the `site_chrome` context processor) sets `aria-current`. Used by the mobile navigation. |
| `includes/nav_mega.html` | none | Desktop primary navigation with mega-menu panels (Firmware, Brands, Chipsets, Tools & Guides); same `nav_current` contract. Panels open on hover or from `[data-mega-toggle]`; Esc closes. |
| `components/command_palette.html` | none (rendered once by `base.html`) | Ctrl/Cmd+K palette: the shared combobox (`input_id="palette-search"`) plus quick links and preference actions. Open from `[data-command-palette-open]`. |
| `components/badge.html` | `variant` (free, paid, featured, verified, popular, new, warning, danger), `label` (required), `icon` | Pill badge (`.badge-ui`). |
| `components/skeleton_cards.html` | none | Six decorative placeholder cards for a loading list. |
| `components/load_more.html` | `url` (required), `target` | Infinite-scroll sentinel (`hx-trigger="revealed"`) with a "Load more" link fallback. |

### Template helpers (`mf_ui`)

- `{{ field|mf_widget }}` renders a bound field with the Bootstrap class for its widget type, `aria-invalid` and `aria-describedby`.
- `{% table_scroll "Label" %}<table>…</table>{% endtable_scroll %}` wraps a wide table in a focusable, labelled `role="region"` scroll container (`.table-scroll`). Use it, or the equivalent `.table-scroll`/`.catalog-table-wrap` markup with `tabindex="0"`, `role="region"` and an `aria-label`, for every table that can overflow.
- `{{ page_obj|elided_pages }}` yields page numbers and the paginator's ellipsis marker.

## Inline field hints, password checklist and social buttons

- **Inline hints** (`frontend/src/modules/inline-validation.js`, loaded wherever a POST/HTMX form exists): uses the Constraint Validation API (required, `type=email|url`, min/max/step, minlength/maxlength, pattern) and writes a friendly message into the field's `[data-field-error]` container (`role="alert"`, referenced by `aria-describedby`, `aria-invalid="true"`). It validates on blur, refreshes while typing once a message shows, validates everything on submit, blocks the submit and focuses the first invalid field. Server errors use the same container. Override a message with `data-msg-required|type|pattern|min|max|step|minlength|maxlength`. Opt out with `data-native-validation` on the form or `formnovalidate` on a button; opt a non-POST form in with `data-inline-validate`. Fields that are `hidden`, disabled or read-only are skipped. Forms get `novalidate` from the shared components (and from the module at runtime), so the browser's own bubble never shows.
- **Password checklist** (`{% password_checklist field %}` from `users_ui`, `modules/password-checklist.js`): one row per configured `AUTH_PASSWORD_VALIDATORS` entry (the minimum length is read from the validator), plus a "passwords match" row. The state comes from `POST /users/password-rules/` (CSRF-protected, debounced, rate limited, `never_cache`, password never logged or echoed), which runs `django.contrib.auth.password_validation` — the only source of truth. Rows carry `data-state` (pending, pass, fail, unchecked) with a distinct icon shape and a visually hidden status word; the summary is `aria-live="polite"`. It is added automatically to `password1` inputs rendered through `account/_field.html` (sign-up, reset, change/set password, profile completion) and by tag to the profile page's change-password form.
- **Social buttons** (`account/_social_buttons.html`, `{% social_providers %}`): every supported provider is always listed on the login and sign-up pages. Configured-state comes only from `apps/users/services/social_providers.py`; an unconfigured provider's button opens an app-owned dialog (`account/_social_unavailable.html`) and submits nothing. See `docs/04-backend/social-sign-in.md`.

## Site shell

- `templates/base.html` blocks: `title`, `meta_description`, `extra_head`, `main` (wraps `content`), `content`, `extra_scripts`.
- `theme-init.js` is a same-origin classic script in `<head>`. It applies the stored theme (`light`/`dark`; absent = auto), `prefers-color-scheme`, the high-contrast choice or `prefers-contrast: more`, and adds the `js` class before first paint. All storage access is guarded.
- Header: two rows at `lg` and wider (brand, global search, command-menu/theme/account controls, then the mega-menu navigation); a toggled panel below `lg` that is always visible and in flow when JavaScript is off.
- Global search is the shared combobox (`modules/combobox.js`) calling `GET /search/suggest/` (central engine, see `docs/04-backend/search.md`); Enter without an active option submits the form to `/search/`. `firmwares:universal_search` and `firmwares:api_search_autocomplete` remain as legacy catalog endpoints. Ctrl/Cmd+K opens the command palette, whose search is the same combobox.
- Feedback: toasts (`#toast-region`, `modules/toasts.js`) carry Django messages, HTMX failures and server `HX-Trigger` toast events (see `design-system.md`); `#global-feedback` remains as an empty live region. `#global-progress` is the body-level `hx-indicator`, so every request shows a loading bar.
- Dialogs: `#dialog-slot` receives HTMX-loaded dialogs and `#app-dialog-template` (from `components/dialog_js_template.html`) is the markup JavaScript confirms clone. See `dialogs.md`.
- Footer information and legal links come from `app.context_processors.INFO_PAGES` / `LEGAL_PAGES` (slugs such as `privacy`, `terms`, `consent` under `/pages/<slug>/`). A link is rendered only while its CMS page is published, so the footer never links to a 404; run `manage.py seed_pages` to publish the starter set.
- allauth pages render inside the shell through `templates/allauth/layouts/base.html` and the Bootstrap element overrides in `templates/allauth/elements/`.
- Every `data-lucide` icon name used in a template must be registered in `frontend/src/modules/icons.js`; `tests/backend/test_frontend_contract.py` enforces it.

## Combobox contract

`templates/components/combobox.html` plus `frontend/src/modules/combobox.js` (Alpine CSP component `combobox`). The server renders the options (`/search/suggest/`, an HTMX partial swapped in on every keystroke, debounced 150 ms); Alpine owns only local state (open, active option, chosen value). No inline script or style, no `x-show`.

Include parameters:

| Parameter | Meaning |
| --- | --- |
| `input_id` (required) | Unique per page; the listbox is `<input_id>-results`, the submitted value lives in `<input_id>-value`. |
| `label` / `name_label` | Visually hidden label, or the accessible name when a visible `<label for>` exists elsewhere. |
| `mode` | `navigate` (options are links, input is `name=q`) or `pick` (default; options set a hidden value named `name`). |
| `kinds` | Comma separated search kinds, e.g. `country`, `calling_code`, `brand`, `model`. |
| `value`, `display`, `placeholder`, `required`, `invalid`, `describedby`, `limit`, `min_chars` | Current value and its label, plus the usual input attributes. Defaults: `min_chars` 2 for navigate and 0 for pick, `limit` 8. |
| `depends_on`, `depends_param` | Cascade: the parent combobox input id and the `filter.<param>` sent to providers (for example brand → model). The child is cleared and disabled until the parent has a value. |
| `detected` | `"true"` when the value came from auto-detection; a better hint may replace it until the user edits the field. |

Behaviour: `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-activedescendant`; Up/Down/Home/End move the active option, Enter chooses, Esc closes, Tab keeps the typed text; a polite status region announces the result count; an empty result shows "Did you mean" links and the server rendered "request it" links. Navigation mode groups options by kind (icon + heading), keeps recent searches in this browser only (opt out with `recent="off"` or from the list) and the header form answers the `/` and Ctrl/Cmd+K shortcuts. Without JavaScript a pick-mode field keeps its server rendered value (and a plain text fallback input). Details: `docs/04-backend/search.md`.

Events (bubbling `CustomEvent`s on the component root): `combobox:select` `{id, name, value, label, data, source}` (`data` is the option's `dataset`, so `data-calling-code` arrives as `callingCode`), `combobox:clear` `{id, name}`. Dispatch `combobox:set` `{value, label, onlyIfEmpty}` on the root to set a value from outside; `modules/profile-locale.js` uses it to fill only EMPTY country and calling-code fields.

Forms use `apps.core.widgets.ComboboxSelect(kinds=…, placeholder=…)`: the field keeps `choices`, so the server validates the submitted code.
