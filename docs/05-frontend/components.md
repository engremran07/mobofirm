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
| `components/form_field.html` | `field` (required BoundField), `help` | Label, widget, `invalid-feedback` (`<id>_errors`) and help text (`<id>_help`); the widget gets `aria-describedby`/`aria-invalid` from `mf_widget`. Checkboxes render as `form-check`. Hidden fields render bare. |
| `components/form_errors.html` | `form` | Non-field errors as `role="alert"`. |
| `components/stat_tile.html` | `label`, `value` (required), `hint` | Static tile; it never lifts on hover. |
| `components/search_box.html` | `action`, `input_id`, `label` (required), `value`, `name` (default `q`), `placeholder`, `hx_target`, `hx_get` | A GET form that works without JavaScript. With `hx_target` the input itself debounces (`input changed delay:350ms`), shows an indicator and pushes the URL. Never use `from:input`, which binds to the first input in the document. |
| `components/spinner.html` | `id` (required), `label` | An `hx-indicator` target. |
| `components/dialog.html` | `dialog_id`, `dialog_title` (required), `dialog_message`, `dialog_size`, `danger`, `dialog_action`, `confirm_label`; blocks `dialog_body`, `dialog_footer` | The generic dialog shell (native `<dialog>` + Alpine `appDialog`). Apps extend it in `templates/<app>/dialogs/` and serve it with `apps.core.dialogs.DialogView` into `#dialog-slot`. See `dialogs.md`. |
| `components/confirm_dialog.html` | `dialog_id`, `dialog_title`, `dialog_message`, `dialog_action`, `confirm_label` (required), `hidden_name`, `hidden_value`, `danger`, `reason_label`, `reason_required`, `confirm_phrase` | The standard confirm: an app-owned `<dialog>` with a CSRF-protected POST form, extending `dialog.html`. Open it with `<button type="button" data-dialog-open="<dialog_id>">`. Esc and the close button close it; a backdrop click closes it unless `danger`. The server still validates the POST. Never use native `confirm()` or the HTMX confirm attribute; use `data-confirm-dialog` or `Dialogs.confirm` instead. |
| `includes/site_search.html` | `search_id` (unique per page) | Header search: a `components/combobox.html` in `navigate` mode fed by the central search engine, inside a plain GET form to `search:results`. |
| `components/combobox.html` | see "Combobox contract" below | The single type-ahead picker (ARIA 1.2 editable combobox) for header search, country/calling-code and catalog pickers. |
| `includes/nav_links.html` | none | Primary links; `nav_current` (from the `site_chrome` context processor) sets `aria-current`. |

### Template helpers (`mf_ui`)

- `{{ field|mf_widget }}` renders a bound field with the Bootstrap class for its widget type, `aria-invalid` and `aria-describedby`.
- `{% table_scroll "Label" %}<table>…</table>{% endtable_scroll %}` wraps a wide table in a focusable, labelled `role="region"` scroll container (`.table-scroll`). Use it, or the equivalent `.table-scroll`/`.catalog-table-wrap` markup with `tabindex="0"`, `role="region"` and an `aria-label`, for every table that can overflow.
- `{{ page_obj|elided_pages }}` yields page numbers and the paginator's ellipsis marker.

## Site shell

- `templates/base.html` blocks: `title`, `meta_description`, `extra_head`, `main` (wraps `content`), `content`, `extra_scripts`.
- `theme-init.js` is a same-origin classic script in `<head>`. It applies the stored theme, or `prefers-color-scheme`, before first paint and adds the `js` class. All storage access is guarded.
- Header: two rows at `lg` and wider (brand, global search, theme and account controls, then navigation); a toggled panel below `lg` that is always visible and in flow when JavaScript is off.
- Global search is the shared combobox (`modules/combobox.js`) calling `GET /search/suggest/` (central engine, see `docs/04-backend/search.md`); Enter without an active option submits the form to `/search/`. `firmwares:universal_search` and `firmwares:api_search_autocomplete` remain as legacy catalog endpoints.
- Feedback: `#global-feedback` is a visible, dismissible alert region for HTMX failures. `#global-progress` is the body-level `hx-indicator`, so every request shows a loading bar.
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

Behaviour: `role="combobox"`, `aria-expanded`, `aria-controls`, `aria-activedescendant`; Up/Down/Home/End move the active option, Enter chooses, Esc closes, Tab keeps the typed text; a polite status region announces the result count; an empty result shows the server rendered "request it" links. Without JavaScript a pick-mode field keeps its server rendered value (and a plain text fallback input).

Events (bubbling `CustomEvent`s on the component root): `combobox:select` `{id, name, value, label, data, source}` (`data` is the option's `dataset`, so `data-calling-code` arrives as `callingCode`), `combobox:clear` `{id, name}`. Dispatch `combobox:set` `{value, label, onlyIfEmpty}` on the root to set a value from outside; `modules/profile-locale.js` uses it to fill only EMPTY country and calling-code fields.

Forms use `apps.core.widgets.ComboboxSelect(kinds=…, placeholder=…)`: the field keeps `choices`, so the server validates the submitted code.
