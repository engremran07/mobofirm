# Staff console (`/admin/`)

The staff console is the operator-facing part of Mobofirm. `django.contrib.admin` stays the
**engine** (model permissions, `admin:` URL names, actions, inlines, history, autocomplete);
everything the operator sees is a project-owned **presentation layer**. `django-unfold` was
removed; `tests/backend/admin/test_no_unfold.py` fails if it, `app/settings/unfold.py` or
`templates/unfold` return. The public site has its own shell (`design-system.md`); the two
share design tokens, the theme preference and the htmx runtime, nothing else.

The stack is approved by the product owner (see `docs/01-product/` and
`.agents/policies/source-of-truth.yaml`): **AdminLTE 4.10.2** (layout, sidebar, treeview, card
widgets) on **Bootstrap 5.3.9**, the shared **htmx 4.0.2** runtime, **`@alpinejs/csp` 3.17.5** and **Lucide 1.77.0**,
all vendored offline by Vite. The rich-text editor (Summernote 0.9.1 + jQuery 3.7.1, see
`editor.md`) is the approved legacy exception: it is a lazy chunk of the *public* build, never a
global, and the console does not otherwise use jQuery.

## Architecture

| Layer | Where | Rule |
| --- | --- | --- |
| Engine | `django.contrib.admin`, `apps.core.admin_site.MobofirmAdminSite` (installed through `apps.core.admin_config.MobofirmAdminConfig`) | Anonymous visitors go to the project login (`users:account_login`, with a safe `next`); the admin never authenticates anyone itself. |
| Python seam | `apps/core/admin_base.py`, `admin_actions.py`, `admin_filters.py`, `admin_forms.py`, `admin_widgets.py` | Apps import **only** `apps.core.admin_base` (and `admin_filters` / `admin_forms` for filters and dialog forms). New admin classes never subclass `django.contrib.admin.ModelAdmin` directly. |
| Navigation / dashboard | `apps/core/admin_navigation.py` + `admin_nav_catalog.py`, `admin_dashboard.py`, `admin_shell.py` (command palette search), `admin_branding.py` | Declared once, permission-aware. |
| Templates | `templates/admin/` (Django override names), `templates/console/` (`components/`, `crud/`, `filters/`, `widgets/`, `staff/`, `dashboard/`, `shell/`, plus `layout.html`, `base_partial.html`, `standalone.html`, `error_page.html`) | Pages compose components; no page-specific CSS. |
| Frontend source | `frontend/src/admin/` (`main.js`, `admin.scss`, `js/`, `styles/`) | One JS entry, one SCSS entry. |
| Build output | `static/build/admin/` (not committed) with `.vite/manifest.json` | Content-hashed file names. |
| Asset tag | `{% console_assets %}` in `apps/core/templatetags/console.py` | Reads the manifest; raises `ConsoleAssetsError` with a "run `npm run build:admin`" message when it is missing. |

### Security

- The console is served under its own policy, `ADMIN_CSP` (`app/settings/security.py`, applied by
  `AdminContentSecurityPolicyMiddleware` to the `admin` and `seo` URL namespaces plus the staff
  views in `ADMIN_SHELL_VIEWS`, e.g. `ads:dashboard`, `tags:manage`, `comments:moderation_queue`,
  `storage:status`). Every other page keeps the public policy.
- Scripts: same-origin only. **No `'unsafe-eval'`, no `'unsafe-inline'`, no nonce for scripts.**
  Styles: same-origin files plus `<style>` elements carrying the per-request nonce; there is no
  `style-src-attr`, so templates must not use `style="..."` attributes (toggle classes or CSSOM
  properties instead). `img-src` allows `self` and `data:`; no third-party host is allowed.
- No inline scripts, no inline event handlers, no CDN, no remote font/image. `npm run check:offline`
  enforces the output is remote-URL free and consistent with the manifest.
- **Staff MFA is mandatory.** `apps/users/services/mfa.py::is_privileged` is true for every staff or
  superuser account unless `settings.STAFF_MFA_REQUIRED` is false; the setting is read with a default of
  true and only `app/settings/test.py` sets it to `False`. `EnforceMfaMiddleware`
  (`apps/users/middleware/mfa_enforce.py`) then requires a verified email, an active TOTP device and a
  verified session (`users:verify_email`, `users:mfa_setup`, `users:mfa_verify`) before any non-exempt page,
  console included. Tests that exercise the real gate set it to `True`
  (`tests/backend/users/test_staff_mfa_required.py`).
- Every HTMX endpoint authorizes on the server (`admin_view`, model permissions, `@action(permissions=)`);
  the UI hiding a control is never the control.

## Asset pipeline

```text
npm install                 # once; Node >=22.12
npm run build:public        # vite build                 -> static/build/*        (stable names, shared htmx 4)
npm run build:admin         # vite build --mode admin    -> static/build/admin/   (hashed names + manifest, shared htmx 4)
npm run build               # both
npm run check:offline       # scripts/check_offline_assets.py via .venv: no remote URLs, manifest consistent
npm run check               # build + check:offline
```

- `vite.config.js` holds both configurations. The public build never wipes `static/build/admin/`
  (it cleans its own files only). The admin build uses `emptyOutDir`, `manifest: true` and
  `assets/[name]-[hash].js|css` naming; inputs are `console-main` (`frontend/src/admin/main.js`) and
  `console-styles` (`frontend/src/admin/admin.scss`).
- Both bundles import the single `htmx.org` 4.0.2 dependency and shared CSP/configuration module
  (`frontend/src/shared/htmx.js`). Keep public interactions and console-only boost behavior in their
  respective shells; do not add a second htmx copy or use htmx 2 event/attribute conventions.
- Rarely used widgets (editor, charts) must be lazy chunks via dynamic `import()`, never added to
  `main.js` statically.
- Fonts: system stack or self-hosted woff2; never a remote font. Icons: tree-shaken Lucide.

### Icons

Only icons listed in `frontend/src/admin/js/icons.js` are bundled. Register a Lucide icon there
before using its kebab-case name in a template (`{% console_icon "plus" %}`, `data-lucide="plus"`) or in
an `@action(icon=...)`. Material-style names used by older actions are translated through
`ICON_ALIASES` in `apps/core/admin_actions.py`.

## Bootstrap 6 upgrade seam

Bootstrap enters the console in exactly two files:

1. `frontend/src/admin/styles/_bootstrap.scss` (the selective Sass partial list, the `$variables`
   configuration and the `--bs-*` bridge to the `--mf-*` tokens, wrapped in `@layer`s);
2. `frontend/src/admin/js/vendor-bootstrap.js` (the JS plugins: Collapse, Tooltip; menus, dialogs and every other control run on `js/ui/`).

Templates use Bootstrap classes and `data-bs-*` attributes **only inside shared components**
(`templates/console/components/`). A 6.0 bump is therefore: change the `bootstrap` pin, update the two
files above and the components, rebuild. The checklist is in the header of `_bootstrap.scss`.
AdminLTE 4 is written against Bootstrap 5.3 and resolves `bootstrap/scss/*` through the same
`node_modules` entry, so check AdminLTE's matching release (or drop its SCSS partials) first.

## htmx 4 conventions

The authoritative list is the header comment of `frontend/src/admin/js/htmx-setup.js`; templates
must follow it. The ones people trip over:

- **Events** use `htmx:phase:action`: `htmx:config:request`, `htmx:before:request`,
  `htmx:after:request`, `htmx:before:swap`, `htmx:after:swap`, `htmx:after:settle`,
  `htmx:response:error`, `htmx:finally:request`. The htmx 2 camelCase names (`htmx:afterSwap`, ...)
  do not exist in either shell.
- **Attribute inheritance is explicit** (`implicitInheritance` stays off): put
  `hx-target:inherited`, `hx-swap:inherited`, `hx-boost:inherited` on the parent. A bare `hx-target`
  on a container is not inherited by its children.
- **No string evaluation** (CSP): the shared runtime rejects expression evaluation and inline event
  binding; use no `hx-on*`, `js:` values, or bracket trigger filters. Scripts and inline handlers are
  stripped from every swap. Use trigger modifiers such as `hx-trigger="input changed delay:250ms"`.
- **Status handling**: htmx 4 swaps everything except 204/304. The console additionally refuses to swap
  401/403/404/405/408/410/413/429/5xx and shows an accessible toast instead. 400/409/422 swap, so
  return the re-rendered form with errors. Per-element override: `hx-status:422="target:#errors"`.
- **Boost** takes an HCON config. `<body>` carries
  `hx-boost:inherited="target:#console-main select:#console-main swap:outerHTML"` and
  `hx-select-oob:inherited="#console-toasts:beforeend"`, so a click on a console link fetches the **full** page and swaps
  only `#console-main` (breadcrumbs + content); Django messages are appended to the toast region, the title comes
  from the response, the sidebar/header are never re-rendered (their state survives). `htmx-setup.js` (`shouldBoost`)
  decides which elements really take part: plain same-origin links under the console prefix
  (`<meta name="console-url-prefix">`). Forms are **not** boosted (CSRF, uploads, MFA, logout keep their native
  POST/redirect; opt in with `data-console-boost`); links with `download`, a file extension, `_popup`, `data-bs-toggle`,
  `data-console-related-link`, `role="button"`, `data-console-no-boost`, inside a modal, or pointing outside
  the console are left alone. Transitions: `ctx.transition` is set only for boosted navigation and
  `[data-console-transition]` regions, only when `startViewTransition` exists and `prefers-reduced-motion` is not
  `reduce` (`_layout.scss` shortens the root cross-fade).
- **Extensions** (`js/htmx-extensions.js`; htmx 4 has no `hx-ext`, loading the script is the activation):
  `hx-head` merges page-specific `<head>` assets (form media, extra styles) of a boosted response into the live
  document, `hx-preload` prefetches a boosted link on `mousedown`/`touchstart` and links marked
  `hx-preload="mouseenter, focusin"` (the whole sidebar) on hover/focus; the click then reuses that response.
  Add new extensions only there and only when a feature needs them (`ext/hx-sse|ws|live|pending|...` stay out of the bundle).
- **History** has no snapshot cache: back/forward refetches the URL with `HX-History-Restore-Request: true`
  and the server must answer with the **full** page.
- **Fragments vs. pages**: `console_layout` (`console_ui.py`) returns the content-only
  `console/base_partial.html` for `HX-Request` calls that are not boosted and not history restores, and the
  full `console/layout.html` otherwise. A view therefore returns one template for both.
- **CSRF**: every non-GET request carries `X-CSRFToken` from `<meta name="csrf-token">`.
- **Server to UI messages** travel as `HX-Trigger` events: `console-toast`, `console-dialog-open`,
  `console-dialog-close`, `console-refresh` (`hx-trigger="console-refresh from:body"` refreshes a region).
- `hx-confirm="text"` opens the app-owned confirm dialog, never `window.confirm`.

## Alpine (CSP build) rules

- `@alpinejs/csp` evaluates no strings. Register every component with `Alpine.data(name, factory)` in
  `frontend/src/admin/main.js` (`registerAlpine()`): `consoleSidebar`, `consoleTheme`, `consoleToasts`,
  `consoleChangelist`, `consoleFormset`, `consoleTabs`, `consoleCombobox`.
- The shell itself is driven by `data-console-*` hooks and document-level behaviours, so Alpine is
  optional: `x-data="consoleSidebar|consoleTheme|consoleToasts"` only exposes reactive state (`collapsed`,
  `open`, `mode`, `dismissAll`, ...) to markup that wants it.
- Templates may reference only **properties and methods by name** (`x-data="consoleSidebar"`,
  `x-on:click="toggle"`); no inline expressions, arithmetic, object literals or arrow functions, and
  no `x-show`/style-attribute bindings (the console CSP has no `style-src-attr`; toggle classes in the component instead).
- Roots that need a controller but not Alpine state use `data-console-*` hooks scanned by
  `js/behaviours.js` (`[data-console-changelist]`, `[data-console-formset]`, `[data-console-tabs]`,
  `[data-console-combobox]`, `[data-console-prepopulated]`, `script#console-conditional-rules`), re-scanned after each
  `htmx:after:swap`.
- `window.mobofirmConsole` exposes `toast`, `confirm`, `openDialog`, `closeDialog`, `openPalette`,
  `setTheme`, `scan` for server-driven UI and tests. Business logic never lives in JavaScript.

## Component catalogue

Components live in `templates/console/components/`. Render them with the isolated tags from
`console_ui` (`{% load console_ui %}`): `{% ui "badge" label="Live" variant="success" %}` for
self-closing and `{% component "card" title="Queue" %}...{% slot "actions" %}...{% endslot %}{% endcomponent %}`
for block components. A component never sees the page's own context. Each file's header comment is the
argument reference.

Fixed dropdown and listbox panels are temporarily portalled to `body` while open, then returned
to their component root on close. This keeps viewport positioning aligned with the trigger even
inside filtered, transformed, or scrolling ancestors; the popover controller preserves ownership
and focus behavior across the move.

| Component | Purpose |
| --- | --- |
| `alert`, `toast` | Inline message / transient notification (`#console-toasts`). |
| `badge`, `stat_tile` | Status pill (colour never the only signal) / KPI tile (`None` renders an em dash, never a fake zero). |
| `button`, `dropdown`, `kebab_menu` | Buttons, dropdown menus (links or CSRF POST items), row action menu. |
| `card`, `page_header`, `breadcrumb`, `tabs`, `tab_panel` | Surfaces and page structure; tabs are navigation tabs or JS-enhanced in-page tabs. |
| `data_table`, `pagination`, `filter_chip` | Responsive scroll-in-container table, pagination (HTMX-aware), removable filter pills. |
| `form_field` | One labelled bound field with help text and errors (`.c-form-grid > .c-field`). |
| `dialog` | Content of `#console-dialog` (a native `<dialog class="modal">`; the `.modal-*` classes are layout only), also a plain page without JavaScript. |
| `empty_state`, `skeleton`, `spinner` | Empty/error/loading states for every data region. |
| `icon` (`{% console_icon %}`) | Registered Lucide icon. |
| `command_palette`, `palette_list` | "Search or jump to" dialog and its result list. |
| `theme_toggle`, `user_menu` | Light/dark/system switch, account menu (sign-out is a POST form). |

The authenticated account menu links to the user's dashboard, profile, wallet (when enabled), contributions and notifications. Sign-out is a CSRF-protected POST guarded by the console's app-owned confirmation dialog.

Related template sets: `console/crud/` (changelist, form, inline, delete and action-dialog building blocks),
`console/filters/` (one template per filter class), `console/widgets/` (form widgets),
`console/dashboard/` (widget templates), `console/shell/` (header, sidebar), and `console/staff/` +
`apps/core/templatetags/staff_ui.py` (`{% staff_card %}`, `{% staff_section %}`, `{% staff_table %}`,
`{% staff_notice %}`, `{% staff_button %}`, `{% staff_input %}`, ... for custom staff pages).

## How to add things

### Import and export for a model

Add `ImportExportMixin` (from `apps.core.admin_base`) before `ModelAdmin`, subclass
`Resource` (declare `columns`, `natural_key_doc`, `export_row`, `clean_row`, `key_of`,
`find`, `changed`, `create`, `update`) and list the two list actions:

```python
class ThingAdmin(ImportExportMixin, ModelAdmin):
    import_export_resource = ThingResource
    actions_list = ("import_data", "export_data")
```

The pages (`templates/console/import_export/`) are composed from `staff_*` components only.
Export needs `view`, import needs `add` or `change`; the engine streams CSV/JSON, escapes
spreadsheet formulas, previews before writing and applies in one atomic transaction (see
`docs/04-backend/firmware-catalog.md`).

### An admin page for a model

Extend `apps.core.admin_base`, never `django.contrib.admin`:

```python
from django.contrib import admin

from apps.core.admin_base import ModelAdmin, display, BADGE_APPROVAL
from apps.core.admin_filters import ChoicesDropdownFilter, RangeDateTimeFilter


@admin.register(Thing)
class ThingAdmin(ModelAdmin):
    list_display = ("name", "status_badge", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name",)

    @display(description="Status", label=BADGE_APPROVAL, ordering="status")
    def status_badge(self, obj):
        return obj.status
```

Then list the model once in `apps/core/admin_nav_catalog.py` (`m("app.thing", "icon")`; an entry shows
only when the model is registered and the user may view or change it). A registered model that no group
lists lands in an "Other" group, and `tests/backend/admin/test_admin_all_models.py` fails while it is
non-empty. Use `NoAddModelAdmin`, `ReadOnlyModelAdmin` or `SingletonModelAdmin` where they fit;
`TabularInline` / `StackedInline` default to `extra=0`, `per_page=25`. Extra `ModelAdmin` attributes
(`actions_list`, `actions_row`, `actions_detail`, `actions_submit_line`, `warn_unsaved_form`,
`list_filter_submit`, `conditional_fields`) are documented in the module docstring of `admin_base.py`.

### An action

```python
from apps.core.admin_base import (
    ActionVariant,
    ReasonForm,
    action,
    change_url,
    confirm_dialog,
    htmx_redirect,
)


@action(
    description="Reject",
    permissions=["change"],
    url_path="reject",
    icon="circle-x",
    variant=ActionVariant.DANGER,
    dialog=confirm_dialog(title="Reject this item?", form_class=ReasonForm),
)
def reject(self, request, form, object_id):
    ...  # call the owning service; it enforces permissions again
    return htmx_redirect(change_url(self, object_id))
```

List it in `actions_row`, `actions_detail`, `actions_list` or `actions_submit_line`. Contract
(`apps/core/admin_actions.py`): a `dialog=` action **requires `permissions=`** (refused at import
otherwise); **GET renders the dialog and never mutates**; the POST (CSRF enforced) validates the form and
only then runs the function. An invalid POST re-renders the dialog. `htmx_redirect(url)` sets `HX-Redirect`
for HTMX; a plain browser POST that returns it receives a **302 instead** (`_post_redirect_get`), so every
successful browser POST ends with POST-Redirect-GET. Row/detail/list actions without a dialog are
navigation-only and must not write.

### A filter

Use `apps/core/admin_filters.py`: `ChoicesDropdownFilter`, `BooleanRadioFilter`, `DropdownFilter`,
`RelatedDropdownFilter`, `FieldTextFilter`, `TextFilter`, `RangeDateFilter`, `RangeDateTimeFilter`,
`RangeNumericFilter`. They are plain Django list filters (stock `list_filter` syntax) rendered by
`templates/console/filters/`. Malformed hand-edited values raise `IncorrectLookupParameters`, so the
changelist redirects to `?e=1` instead of a 500. A new filter type adds a class there, a template under
`templates/console/filters/`, and a row in the table in that module's docstring.

**Filter bar contract.** Every changelist shows its filters in a horizontal bar above the results
(`#c-cl-filters.c-fbar`, between the search toolbar and the active-filter chips); the query-string contract is
unchanged. Each filter template renders one `div.c-fctl[data-ui-popover]`: a `button[data-ui-trigger]`
(`aria-haspopup`, `aria-expanded`, `aria-controls` = the panel id) and a hidden `[data-ui-panel]`. Choice filters
(`_choices.html`) hold `ul[role=listbox] > li[role=presentation] > a[role=option][aria-selected]` links (each is a
normal GET that HTMX turns into a results swap); form filters (`_range.html`, `text.html`) hold a labelled
`role=dialog` panel with a GET form that carries the other active parameters. Below 768 px the bar collapses
behind one `Filters (n)` button and opens as a bottom sheet (`[data-ui-sheet]`, `[data-ui-sheet-toggle]`). HTMX
swaps `#c-cl-results` and refreshes `#c-cl-filters`, `#c-cl-chips`, `#c-cl-status` (`aria-live=polite`) out of band
and pushes the URL. `tests/backend/admin/test_filter_bar_markup.py` asserts the markup contract per filter class;
`tests/frontend/test_admin_filters_bulk_browser.py` drives it in Chromium.

### Interaction primitives (Headless UI convention)

`frontend/src/admin/js/ui/popover.js` provides the behaviour of a popover / listbox / menu / sheet and nothing
visual: state is exposed as data attributes and styled only in SCSS from the shared tokens.

| Attribute | Meaning |
| --- | --- |
| `data-ui-popover` / `data-ui-trigger` / `data-ui-panel` | root, trigger button, panel (`hidden` when closed) |
| `data-open` (root) | the panel is shown (`aria-expanded="true"` on the trigger) |
| `data-active` (item) | the item that has keyboard focus or the pointer |
| `data-selected` / `data-disabled` (item) | server-rendered state of an option |
| `data-focus` (root) | focus is inside the root; `data-align="end"` flips the panel to the end edge |

Keyboard: Enter/Space/ArrowDown/ArrowUp open; Arrow keys, Home/End and type-ahead move between items; Escape closes
and returns focus to the trigger; Tab and outside clicks close.

**Catalogue** (`frontend/src/admin/js/ui/popover.js` and `controls.js`, markup in `templates/console/components/`,
look in `styles/components/_filter-bar.scss` and `_controls.scss`). All share the data-attribute convention above
(`data-open`, `data-active`, `data-selected`, `data-checked`, `data-disabled`, `data-focus`).

| Primitive | Markup contract | Notes |
| --- | --- | --- |
| Menu / kebab / user / widget menus | `dropdown.html`, `kebab_menu.html`, `user_menu.html`, `crud/_action.html`, row actions in `crud/results_table.html` | `role=menu`, items get `role=menuitem`; panels use `data-ui-fixed`, clamp to the viewport, flip above when needed and scroll when taller than the available space |
| Listbox / select | `{% include "console/components/listbox.html" with field=form.field %}` | the native `<select>` stays the submitted value and the no-JS fallback; used for the bulk-action selector |
| Popover (filters) | `console/filters/*.html` | listbox popovers and form popovers |
| Switch | `switch.html` (`input[role=switch][data-ui-switch]`) | native checkbox is the value; Space toggles |
| Radio group | `radio_group.html` (`role=radiogroup`, roving tabindex) | Arrow/Home/End select; hidden input carries the value |
| Combobox | `widgets/autocomplete.html` + `js/components/combobox.js` | HTMX-free JSON fetch from Django's autocomplete endpoint; exposes `data-open` / `data-active` / `data-selected` |
| Dialog / offcanvas | `dialog_native.html` (`<dialog data-ui-dialog>`, `--end` = offcanvas) | browser focus trap + Escape; focus restored to the opener |
| Tabs / disclosure | `tabs.html`, `<details>` | unchanged |

Why plain delegated JavaScript instead of `Alpine.data` components: these controls are server-rendered and swapped
by HTMX; document-level delegation survives swaps with no per-fragment `x-data` bootstrap and no expression
evaluation (strict CSP), and keeps the initial bundle small. Bootstrap's JS now only serves `Collapse` and tooltips:
`Dropdown` and `Modal` are no longer bundled.

**Dialogs.** Every modal (the `#console-dialog` host, the confirm dialog, the command palette, the shortcuts help) is a
native `<dialog class="modal">` driven by `frontend/src/admin/js/ui/modal.js` (`NativeModal`: `show()`, `hide()`,
`dispose()`; events `show|shown|hide|hidden.ui.modal`, `hide` cancelable for the unsaved-changes guard). The browser
provides the focus trap, inert background and `Escape`; the host adds backdrop-click close (not for `static`
dialogs), restores focus to the opener (or, when the opener lived in a menu that closed, to that menu's button) and
keeps the `[open]`, `data-open` and `.show` state hooks. `[data-ui-dismiss="modal"]` (legacy `data-bs-dismiss`) closes
the nearest dialog. The `confirm_dialog` contract and `data-console-confirm*` attributes are unchanged.

**Switches and radio groups on forms.** Boolean fields on the settings singleton pages (`SingletonModelAdmin` sets
`console_switches`) and boolean `list_editable` columns render the SAME native checkbox with `role="switch"` and the
`c-switch-input` class; `RadioSelect` widgets render as a segmented control over the SAME native radios
(`console/widgets/radio_select.html`). Names, values and checked state are unchanged, so no backend code changes and
the controls work without JavaScript; `controls.js` mirrors state to `data-checked` / `data-selected`.

**Shared styling.** `shared/components/_shared.scss` also exports `card-surface` (public `.surface-card`, console
`.c-card`) and `control-base` (public and console `.form-control` / `.form-select`); buttons still keep separate
public and console partials.

**Bulk action bar.** `.c-bulkbar` is a raised-surface (`--mf-surface-raised`, `--mf-border-strong`, radius-md,
elevation-3) sticky bar, never a pill: selected count (`aria-live=polite`), "Select all N" across pages, the action
`select`, `Run` (disabled and readable until an action is chosen, with a `title` explaining why), and "Clear
selection". The results container keeps bottom padding while the bar is visible.

### A component

1. Create `templates/console/components/<name>.html` with a header `{% comment %}` listing its context.
2. Put Bootstrap classes and `data-bs-*` only here, never in page templates.
3. Style it in `frontend/src/admin/styles/components/_<name>.scss` using `--mf-*` tokens
   (`styles/_tokens.scss`), logical properties and the 150-200 ms motion tokens; import it from `admin.scss`.
4. Behaviour: prefer HTMX attributes; otherwise a `data-console-*` hook in `js/behaviours.js` or an
   `Alpine.data` component (CSP rules above). Register new Lucide icons in `js/icons.js`.
5. Run `npm run build:admin`, then the console tests.

### A staff page outside the model admin

Extend `admin/staff_page.html` and render with `apps.core.admin_pages.render_admin_page`; compose the body
from `{% staff_* %}` tags and `console/staff/*` includes. Dialogs subclass `StaffDialogView`
(`apps/core/admin_dialogs.py`) and open into `#console-dialog`. Put the page's URL name in
`ADMIN_SHELL_VIEWS` (`apps/core/middleware/admin_csp.py`) when it lives outside the `admin`/`seo`
namespaces so it is served under `ADMIN_CSP`.

### The navigation sidebar

`templates/console/shell/sidebar.html` + `js/components/sidebar.js` + `.c-nav-*` in `styles/_layout.scss`. Groups come
from `apps/core/admin_nav_catalog.py` / `admin_navigation.py` (`key`, `title`, `icon`, `items`, `collapsible`, `open`, `active`).

- Each collapsible group is a disclosure: `li[data-console-nav-group] > button.c-nav-toggle[aria-expanded][aria-controls]`
  + `div.c-nav-panel[data-open]` (animated with `grid-template-rows`; closed panels are `visibility:hidden`, so their links
  leave the tab order). Enter/Space toggle; arrow keys, Home and End walk the visible entries; Escape clears the filter.
- State is per user in `localStorage` (`mobofirm-console-nav-groups:<user pk>` and the rail choice
  `mobofirm-console-sidebar`) and is re-applied after every boosted swap and history restore. Only one group can stay
  open at a time; older stored multi-open preferences are reduced to the active group (or the first remembered group). The group
  holding the current page (the link with `aria-current="page"`, longest matching path) is always opened.
- Tools: filter box (`[data-console-nav-filter]`, narrows entries and opens matching groups) and collapse all
  (`[data-console-nav-all="collapse"]`).
- Desktop icon rail (`html[data-console-sidebar="mini"]`, body `sidebar-collapse`): labels stay in the accessibility tree
  (visually hidden), `data-nav-label` feeds the tooltip (`.c-nav-tip`), and a click on a group icon reopens the sidebar on that
  group. Mobile (< 992 px): off-canvas drawer (`data-console-sidebar="open|closed"`), closed by Escape, the scrim, or following a link.
- `<html data-console-sidebar>` is the *state* attribute; the element itself is `aside[data-console-sidebar]`: always scope selectors to `aside`.
- Without JavaScript (`html:not(.js)`) every group is expanded and the tools are hidden.

### A dashboard widget

Add a `Widget(...)` to `WIDGETS` in `apps/core/admin_dashboard.py` (`slug`, `title`, `icon`, `template`,
`build`, `skeleton`, optional `permissions`, `size`, `refresh`). The dashboard lazy-loads each widget from
`admin:console_dashboard_widget`; an unavailable metric renders an em dash, never a fabricated zero.
Widget markup contract (`js/components/widgets.js`): placeholder and fragment carry `data-console-widget="slug"`; the
fragment also `data-console-refresh="<seconds>"` (from `Widget.refresh`, 0 = never), `hx-trigger="console-refresh from:body, console-widget-refresh"`
and `hx-sync="this:replace"`. `widgets.js` publishes `data-state="loading|error"` + `aria-busy`; on any failed request
(network error, 4xx/5xx, timeout) the `_widget_error.html` block (message + "Try again", its own `hx-get`) replaces the skeleton,
so a widget never hangs on the loading state. Automatic refresh runs every `refresh` seconds (minimum 15, checked every 10 s),
is paused while the tab is hidden (`document.hidden`), skips widgets that are loading or in the error state, and catches up
immediately when the tab becomes visible again.

## Quality bars

WCAG 2.2 AA contrast and focus, keyboard operable, responsive from 360 px with no page-level horizontal
overflow (tables scroll inside their container), light and dark themes (the shared `mobofirm-theme`
preference, resolved to `<html data-bs-theme>`, with `prefers-color-scheme` when unset), reduced motion
respected, logical properties, no console errors, CSP violations or 404 assets on any console URL.

## Verification

All commands use the repository virtualenv; Playwright needs `.venv\Scripts\python.exe -m playwright install chromium` once.

```powershell
npm run check                                              # build both bundles + offline check
.venv\Scripts\python.exe -m pytest tests/backend/admin -q  # contracts, CRUD render/round-trip, assets, no-unfold
.venv\Scripts\python.exe -m pytest tests/frontend/test_admin_console_nav.py -q           # retractable sidebar, boosted navigation, widget retry/refresh
.venv\Scripts\python.exe -m pytest tests/frontend/test_admin_console_browser.py tests/frontend/test_admin_console_interactions.py tests/frontend/test_admin_console_theme_browser.py tests/frontend/test_staff_pages_browser.py -q
.venv\Scripts\python.exe -m pytest tests/frontend/test_admin_console_crawl.py -q          # every console URL, 1280/768/360 px, light + dark
.venv\Scripts\python.exe -m pytest tests/frontend/test_admin_console_crawl.py -k "test_crawl_group and blog" -q   # one group while iterating
.venv\Scripts\python.exe -m pytest -q                      # full suite before reporting
```

`test_admin_console_crawl.py` derives its URL world from `admin.site._registry` (changelist, add, change,
history, delete, HTMX fragment and empty-result state of every model) plus every other named `admin:` route
and the curated staff pages, and writes a JSON report to `RESULTS_PATH` in
`tests/frontend/console_browser_support.py` (`crawl-results.json`; a subset run writes
`crawl-results-partial.json` next to it so it never overwrites the full report). The report goes to the
system temp directory unless `CONSOLE_CRAWL_RESULTS` points elsewhere; it is an artifact, never committed.
`tests/backend/admin/test_console_assets.py` covers the manifest and `{% console_assets %}`.
