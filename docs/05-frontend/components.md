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
| `components/confirm_dialog.html` | `dialog_id`, `title`, `message`, `action`, `confirm_label` (required), `hidden_name`, `hidden_value`, `danger` | App-owned `<dialog>` with a CSRF-protected POST form. Open it with `<button type="button" data-dialog-open="<dialog_id>">`. Cancel, Esc and a backdrop click close it. The server still validates the POST. Never use `confirm()`. |
| `includes/site_search.html` | `search_id` (unique per page) | Header search with autocomplete. |
| `includes/nav_links.html` | none | Primary links; `nav_current` (from the `site_chrome` context processor) sets `aria-current`. |

### Template helpers (`mf_ui`)

- `{{ field|mf_widget }}` renders a bound field with the Bootstrap class for its widget type, `aria-invalid` and `aria-describedby`.
- `{% table_scroll "Label" %}<table>…</table>{% endtable_scroll %}` wraps a wide table in a focusable, labelled `role="region"` scroll container (`.table-scroll`). Use it, or the equivalent `.table-scroll`/`.catalog-table-wrap` markup with `tabindex="0"`, `role="region"` and an `aria-label`, for every table that can overflow.
- `{{ page_obj|elided_pages }}` yields page numbers and the paginator's ellipsis marker.

## Site shell

- `templates/base.html` blocks: `title`, `meta_description`, `extra_head`, `main` (wraps `content`), `content`, `extra_scripts`.
- `theme-init.js` is a same-origin classic script in `<head>`. It applies the stored theme, or `prefers-color-scheme`, before first paint and adds the `js` class. All storage access is guarded.
- Header: two rows at `lg` and wider (brand, global search, theme and account controls, then navigation); a toggled panel below `lg` that is always visible and in flow when JavaScript is off.
- Global search uses HTMX against `firmwares:api_search_autocomplete`. `modules/search.js` converts the JSON response to DOM nodes (no server HTML is inserted) and implements the combobox/listbox keyboard pattern. Enter without a selection submits the form to `firmwares:universal_search`.
- Feedback: `#global-feedback` is a visible, dismissible alert region for HTMX failures. `#global-progress` is the body-level `hx-indicator`, so every request shows a loading bar.
- Footer information and legal links come from `app.context_processors.INFO_PAGES` / `LEGAL_PAGES` (slugs such as `privacy`, `terms`, `consent` under `/pages/<slug>/`). A link is rendered only while its CMS page is published, so the footer never links to a 404; run `manage.py seed_pages` to publish the starter set.
- allauth pages render inside the shell through `templates/allauth/layouts/base.html` and the Bootstrap element overrides in `templates/allauth/elements/`.
- Every `data-lucide` icon name used in a template must be registered in `frontend/src/modules/icons.js`; `tests/backend/test_frontend_contract.py` enforces it.
