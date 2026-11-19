# Frontend Design System

## Status and approved direction

The product owner explicitly authorized implementation of the initial frontend foundation using Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide icons, and Vite. The approved architecture is hybrid: one centralized token and static-asset source shared by the public experience and admin, with distinct public/admin shells. The first-pass visual direction is light-first editorial with a balanced, multi-tone dark theme; final visual calibration and brand assets remain open for product review.

The system is not a finished visual identity. Do not claim final design approval, invent brand assets, or alter user-facing policy under this technical approval.

## Ownership

- Shared build entries, tokens, components, and behavior live in `frontend/`; built assets live under Django's configured static build directory.
- The public site and the staff console are separate shells. The public shell uses the Vite entries in `frontend/` (`npm run build:public`, htmx 2.0.11 through the `htmx2` alias); the console has its own Vite entry, `frontend/src/admin/`, built by `npm run build:admin` (AdminLTE 4.10.2, Bootstrap 5.3.8, htmx 4.0.2, `@alpinejs/csp`, Lucide; see `admin-console.md`). Django admin remains the permission and persistence authority.
- Public app templates extend the shared public base; admin-hosted staff pages extend `admin/staff_page.html` (which extends `admin/base_site.html`, the console shell). App-specific styles are namespaced extensions, never copies of shared assets.
- Do not add a competing CSS framework, a second icon library, per-app static duplicates, or an app-local build pipeline.
- Split large design files by responsibility when they exceed the repository's configured size budgets; splitting must preserve one canonical implementation.

## Current interaction and accessibility baseline

- Use semantic headings, landmarks, labels, keyboard-operable controls, visible focus, and status announcements.
- Preserve responsive behavior, reduced-motion preferences, and meaningful empty, loading, and error states.
- Keep server authorization, validation, and persisted state in Django. Alpine is limited to ephemeral interface state; HTMX responses remain server-rendered and progressively enhanced.
- Strict CSP forbids inline styles and scripts. Use centralized styles and CSP-compatible Alpine expressions.
- Same-origin requests do not require CORS. Any cross-origin resource or provider needs explicit security review and a narrow CSP allowlist.

## Themes, contrast and shared components

- **One token source.** Every colour (light, dark, system, high contrast) and every non-colour token (type, space, radius, elevation, motion, z-index, focus ring) is defined once in `frontend/src/shared/tokens/` (`_palette.scss` = the only place a hex may be written, `_emit.scss` = the emitters). The public bundle (`frontend/src/styles/tokens.scss` → `static/build/tokens.css`) and the console bundle (`frontend/src/admin/styles/_tokens.scss`, inside `@layer tokens`) both call the same `emit.theme-tokens` / `emit.scale-tokens` mixins; neither file contains a value. Bootstrap's `--bs-*` variables are bridged from the tokens in `_emit.scss`, and the console's Bootstrap Sass variables read `_palette.scss` (`admin/styles/_bootstrap.scss`).
- **Drift guard.** `tests/backend/admin/test_admin_theme.py` fails when a colour literal (`#hex`, `rgb()`, `hsl()` without `var()`) appears anywhere in `frontend/src`, `templates`, `apps` or `app` outside the two token files (the short, reasoned `LITERAL_ALLOWLIST` covers generated brand-logo artwork, QR modules and the web-app manifest, whose defaults are asserted equal to the tokens), when light and dark stop defining the same token set, when the brand anchors (teal `#0b7376` light / `#72d9ce` dark) move, and when any text/surface/accent/status pair (including status ink on its soft tint and the high-contrast variants) drops below WCAG AA. `tests/frontend/test_design_unity_browser.py` checks the computed colours of key elements in both shells and themes.
- **How to add a colour:** add `name: #rrggbb,` to `$light` and `$dark` (and the high-contrast maps for text/surface colours) in `_palette.scss`, use `var(--mf-name)`, and add its pair to `CONTRAST_PAIRS` in the guard test. **How to add a component:** put design decisions shared by both surfaces (status tones, focus ring, shimmer) in `frontend/src/shared/components/_shared.scss` as mixins (`shared.each`, `shared.focus-ring`, `shared.shimmer`) and consume them from the public (`.badge-ui`) and console (`.c-badge`, `.c-alert`, `.c-skeleton`) partials; a new tone, size or density is a modifier added there. The shared layer emits no CSS itself, so it never grows a bundle.
- The theme is resolved before first paint by `theme-init.js` (stored choice, otherwise `prefers-color-scheme`); the toggle stores an explicit choice and storage failures are tolerated. The admin keeps its own light/dark/system choice (`adminTheme`) and synchronises explicit choices with this `mobofirm-theme` key (see "Admin console").
- Reusable partials, template helpers and shell behaviour are documented in `components.md`. Reuse them rather than re-implementing breadcrumbs, pagination, empty states, form fields, scrollable tables or confirmation dialogs.
- Only `a.surface-card` and `.surface-card--interactive` lift on hover; static cards (forms, stat tiles) do not.
- Unfilled ad slots render nothing; `.ad-placeholder` is hidden as a safety net.

## Public design system (Wave 10)

The public shell is a premium, light-first editorial UI with a balanced multi-tone dark theme, built from CSS custom properties (the runtime theme API), SCSS partials compiled by Vite, HTMX for server interaction and Alpine's CSP-safe build for ephemeral state. It is backward compatible: Bootstrap utilities and every existing class (`surface-card`, `hero-panel`, `site-nav-link`, `btn-*`, `form-control`, …) keep working and are re-skinned by the layers below; new pages use the `*-ui` components documented here.

### Source layout and build

```
frontend/src/styles/
  tokens.scss           public entry for the shared tokens (no values of its own; see frontend/src/shared/tokens/)
  main.scss             entry (compiled to static/build/site.css), layer order below
  abstracts/            Sass-only breakpoints + mixins (motion, focus-ring, glass, hover-lift, busy-button)
  base/                 element styles, typography, selection, scrollbars, forced-colours
  legacy/_site.scss     page-level rules from the pre-SCSS stylesheet (migrate page by page)
  layout/_shell.scss    header, mega menu, footer, appearance controls, progress bar
  components/           buttons, forms, cards, badges/tabs/accordion/tooltip/toast, data (skeleton,
                        tables, pagination, filters, stepper), overlays (hero, command palette,
                        sheet), dialog
  utilities/            small helpers (stack, cluster, elev-*)
  themes/               per-theme refinements and the reduced-motion switch
```

Layer order is tokens → base → legacy → layout → components → utilities → themes, imported by `main.scss`; Bootstrap's minified CSS is imported first in `site.js`, feature stylesheets (`blog.css`, `ecosystem.css`, …) after. Output names in `static/build/` are unchanged (`site.css`, `site.js`, `tokens.css`, `theme-init.js`, admin and SEO files). `tokens.css` is compiled from `tokens.scss` (Vite input `tokens`) and stays a separate stylesheet so it loads before `site.css`. Run `npm run build` after any change (do not run two builds at once: `emptyOutDir` makes concurrent builds clash).

### Tokens

| Group | Tokens |
| --- | --- |
| Colour (`shared/tokens/_palette.scss`) | `--mf-page/surface/surface-raised/surface-soft/border/border-strong/text/text-muted/accent/accent-strong/accent-ink/accent-warm/accent-cool/link-hover/success/warning/danger/info/focus` (+ `-soft` status tints), `--mf-glass-bg(-strong)/glass-border`, `--mf-overlay`, `--mf-skeleton-base/shine`, `--mf-shadow-rgb`, `--mf-white/black`, medal and content-colour palettes |
| Type (`_emit.scss`) | fluid `--mf-text-2xs … --mf-text-5xl` (public) and `--mf-fs-xs … --mf-fs-3xl` (console), `--mf-leading-*`, `--mf-tracking-*`, `--mf-font-sans/display/mono` |
| Space / radius | `--mf-space-1…9` (4 px scale), `--mf-section-gap`, `--mf-radius-xs…xl/pill` |
| Elevation | `--mf-elev-1…4`, `--mf-glow`, `--mf-inset-highlight` (colour-aware via `--mf-shadow-rgb`) |
| Glass / gradient | `--mf-glass-blur`, `--mf-gradient-accent/soft/mesh` (derived from the accent, so a site-settings accent restyles them) |
| Motion | `--mf-duration-fast/base/slow`, `--mf-ease-out/spring/in-out` (all durations collapse to 0.01 ms under reduced motion) |
| Layers | `--mf-z-header/mega/sheet/toast/progress`, `--mf-header-height` |

Accent colours come from one source: `site_settings` emits `--mf-accent`, `--mf-accent-strong`, `--mf-accent-cool` overrides and every gradient, glow and tint is derived with `color-mix()`. Add a new foreground/background pair to the contrast test in `tests/backend/test_frontend_contract.py` when you introduce one.

### Themes, contrast and motion

`<html>` carries `data-bs-theme="light|dark"` (resolved), `data-theme-mode="auto|light|dark"` (the visitor's choice), `data-contrast="high"` (high-contrast tokens) and optionally `data-motion="reduce"`. `theme-init.js` applies them before first paint (stored choice → `prefers-color-scheme`; `prefers-contrast: more` turns contrast on unless the visitor chose otherwise). Controls: `[data-theme-toggle]` flips light/dark, `[data-theme-set="auto|light|dark"]` and `[data-contrast-toggle]` (footer "Appearance", command palette). Keys: `mobofirm-theme` (absent = auto) and `mobofirm-contrast` (`high|normal`); all storage access is guarded and other tabs stay in sync. Motion is wrapped in the `motion` mixin (`prefers-reduced-motion: no-preference` and not `data-motion="reduce"`), so reduced-motion visitors never get transitions or animations.

### Components (CSS + Alpine/HTMX)

Every snippet works under the strict CSP (no inline styles or scripts).

- **Header + mega menu**: `includes/site_header.html`, `includes/nav_mega.html`. Top-level links stay plain `<a class="site-nav-link">`; Firmware, Brands, Chipsets and Tools & Guides add a panel opened by hover or by the toggle button (`[data-mega-toggle]`; Esc closes and returns focus). The mobile navigation (`includes/nav_links.html`) has no panels. Set `nav_current` to mark the section.
- **Command palette** (Ctrl/Cmd+K, or `[data-command-palette-open]`): `components/command_palette.html`. Its search is the shared combobox (`input_id="palette-search"`, navigate mode, central search engine), so ranking, suggestions and typo tolerance come from `apps/core/search`; the palette only adds quick links and preference actions. Esc closes suggestions, then clears the query, then closes the palette. It does not open over another modal dialog.
- **Buttons**: `.btn` (+ `btn-primary`, `btn-outline-secondary`, `btn-soft`, `btn-ghost`, `btn-sm`, `btn-lg`, `btn-icon`). Loading: `.is-loading`, `aria-busy="true"`, or automatically `[data-loading]` while the button (or its form) issues an HTMX request.
- **Forms**: `.form-control`, `.form-select`, floating labels (`<div class="form-floating"><input … placeholder=" "><label>`), `.form-layout` grid, `.form-section`, sticky `.form-actions`, `.field-error`, switches (`.form-switch`). Use `components/form_field.html` so labels, errors and descriptions are wired.
- **Cards**: `.card-ui` with `__media/__body/__eyebrow/__title/__text/__meta/__footer/__link` and variants `--firmware --brand --chipset --article --package(.is-featured) --bounty`; grid `.card-grid`. A stretched `.card-ui__link::after` makes the whole card clickable without nesting interactive content; hover lift and focus ring are built in. The legacy `.surface-card` (and `a.surface-card`) gets the same look.
  ```html
  <article class="card-ui card-ui--firmware">
    <div class="card-ui__body"><span class="card-ui__eyebrow">Samsung</span>
      <h3 class="card-ui__title"><a class="card-ui__link" href="…">SM-A546E_XXU3AWK1</a></h3></div>
    <div class="card-ui__footer">{% include "components/badge.html" with variant="free" label="Free" %}</div>
  </article>
  ```
- **Badges**: `components/badge.html` (`variant` = free, paid, featured, verified, popular, new, warning, danger) or `.badge-ui .badge-ui--free`.
- **Tabs**: `<div class="tabs-ui" role="tablist" data-tabs>` with `role="tab"` buttons (`aria-controls`) and `role="tabpanel"`; `modules/ui.js` adds roving focus (arrows, Home, End).
- **Accordion**: `.accordion-ui > details > summary + .accordion-ui__body` (native, keyboard and no-JS safe).
- **Tooltip / popover**: `data-tooltip="…"` on a focusable element (CSS only); popovers use the native API: `<button popovertarget="p1">` + `<div id="p1" popover class="popover-ui">`.
- **Toasts**: `modules/toasts.js`. Django messages (`includes/messages.html`, `data-flash`) become toasts; HTMX failures (`htmx:responseError`, `sendError`, `timeout`) raise a danger toast; a server response can send `HX-Trigger: {"toast": {"message": "Saved", "level": "success"}}` (levels `success|info|warning|danger`); JavaScript calls `showToast({message, level})` or dispatches `mf:toast`. `DialogView` successes that stay on the page use this automatically. Region: `#toast-region` (live region); toasts pause on hover/focus and repeated identical toasts replace each other.
- **Loading**: the top bar (`#global-progress`) shows for every HTMX request; `[data-loading]` marks the requesting element; `.skeleton-host` dims and shimmers while a request targeting it runs; `.skeleton`, `.skeleton--text/title/media/avatar/pill`, `.skeleton-card` and `components/skeleton_cards.html` are placeholders.
  ```html
  <div id="results" class="skeleton-host" hx-get="…" hx-trigger="input changed delay:200ms from:#q" hx-target="this">…</div>
  ```
- **Empty state / stat tile / section header**: `.empty-ui`, `.stat-ui`, `.section-ui` (the existing `empty_state.html` and `stat_tile.html` partials are re-skinned).
- **Tables**: `.table-scroll` / `.table-sticky` (+ `.table-ui`) give a rounded scroller; `.table-sticky` keeps the header visible.
- **Pagination and infinite scroll**: `components/pagination.html` (pill pagination, HTMX-aware). Infinite scroll: `components/load_more.html` renders a sentinel with `hx-trigger="revealed"` and a "Load more" link fallback; the server returns the next rows plus a new sentinel.
- **Filters**: `.filter-bar` (sticky glass bar), `.chip-ui` (`aria-pressed`/`.is-active`, `.chip-ui__remove`), `.chip-row`.
- **Sheets and dialogs**: `<dialog class="sheet-ui">` is a modal on desktop and a bottom sheet on phones; app dialogs use the shared shell (`dialogs.md`).
- **Stepper / breadcrumbs / hero**: `.stepper-ui` (`.is-done`, `.is-current`), `.breadcrumbs` (existing partial), `.hero-ui` / `.hero-panel` with `.hero-ui__title`, `.text-gradient`, `.hero-ui__actions/stats`.
- **Scroll reveal**: `data-reveal` (staggered, once). **Copy to clipboard**: `data-copy="text"`, `data-copy-target="#id"` or `data-copy-url`. **Optimistic UI**: `data-optimistic` (+ `data-optimistic-toggle="aria-pressed"`) flips immediately and reverts on failure. **View transitions**: opt in per swap with `hx-swap="innerHTML transition:true"` (plain swap where unsupported; disabled under reduced motion).
- **Footer**: three-column grid, version stamp, appearance controls.

### Verification

`tests/backend/test_frontend_contract.py` (tokens/contrast incl. high contrast, shell and partial contracts), `tests/frontend/test_design_system_browser.py` (tokens, theme persistence, palette, mega menu, toasts, loading, reduced motion, components, no console/CSP errors, 360/768/1440 overflow), `tests/frontend/test_public_shell_browser.py` and `tests/frontend/test_dialogs_browser.py`.

## Admin console

Full reference: [`admin-console.md`](admin-console.md) (architecture, htmx 4 and Alpine CSP conventions, component catalogue, asset pipeline, Bootstrap 6 upgrade seam, how to add pages/components/filters/actions, test commands). Summary:

The staff console (`/admin/`) keeps `django.contrib.admin` as the engine on a project `AdminSite` (`apps.core.admin_site.MobofirmAdminSite`, installed through `apps.core.admin_config.MobofirmAdminConfig`) and renders it with its own presentation layer (`templates/admin/`, `templates/console/`, `frontend/src/admin/`). The legacy pre-console overrides (`frontend/src/admin.js`, `frontend/src/styles/admin.css`, the old `build/admin.*` entry and every `mf-admin-*` / `mf-review-workflow` class) are gone; `tests/backend/admin/test_admin_access.py` fails if they return, and `tests/backend/admin/test_no_unfold.py` fails if the removed third-party admin theme returns.

- **Tokens and theming**: `frontend/src/admin/styles/_tokens.scss` anchors on the public tokens (`--mf-accent`, `--mf-accent-strong`, light and dark) and `tests/backend/admin/test_admin_theme.py` fails if the two drift. Name, header, logo, dark logo and favicon come from the runtime site-settings accessor through `apps/core/admin_branding.py`.
- **Base classes** are in `apps/core/admin_base.py` (`ModelAdmin`, `NoAddModelAdmin`, `ReadOnlyModelAdmin`, `SingletonModelAdmin`, `TabularInline`, `StackedInline`, `RelatedLink`, `RelatedOverviewMixin`, `ConfirmForm`, `ReasonForm`, `BaseDialogForm`, `confirm_dialog`, `action`, `ActionVariant`, `display`, `htmx_redirect`, `return_url`, `change_url`, `changelist_url`, badge maps); filters are in `apps/core/admin_filters.py`. Every `apps/*/admin.py` imports from there, never from `django.contrib.admin` base classes directly.
- **Navigation**: `apps/core/admin_navigation.py` resolves the sidebar catalogue declared once in `apps/core/admin_nav_catalog.py` (`NAV_GROUPS`). An entry shows only when its model is registered and the user may view or change it. A registered model that no group lists would be appended to an "Other" group; `test_admin_all_models.py`/`test_admin_access.py` fail if that group is ever non-empty. Status tabs cover firmware types, comment/blog/SEO/order/listing statuses and tag state.
- **Dashboard**: `apps/core/admin_dashboard.py` computes cached aggregate counts (60 s) and declares the lazy-loaded widgets (`WIDGETS`); the templates render only the KPI tiles and tables the user has permission for. An unavailable metric renders "—", never a fabricated zero.
- **Workflows**: state changes are `@action`s. Bulk actions keep their names; row/detail actions open the console's accessible dialog (loaded into `#console-dialog`) on GET and change state only on the dialog's CSRF-protected POST, which answers with `HX-Redirect` for htmx and a 302 for a plain browser POST (blog publish/schedule/archive/request review, comment approve/reject/spam with a note, tag approve/reject/merge, device trust/block/unblock/revoke, ad scan discovery approve/reject). Services still enforce permissions; the admin only adds a UI.
- **CSP**: the console is served under its own policy (`ADMIN_CSP` in `app/settings/security.py`, applied by `AdminContentSecurityPolicyMiddleware` to the `admin` and `seo` URL namespaces and the staff views listed in `ADMIN_SHELL_VIEWS`; every other page keeps the strict public policy). Scripts are same-origin only: no `'unsafe-eval'`, no inline scripts, no nonce needed. Styles are same-origin files plus `<style>` elements carrying the per-request nonce; there is no `style-src-attr`. No third-party host is allowed. The public policy is unchanged and `test_admin_csp_is_strict_and_distinct_from_the_public_policy` asserts both.
- **Staff MFA** is mandatory for every staff or superuser account (`apps/users/services/mfa.py::is_privileged`, `STAFF_MFA_REQUIRED`, disabled only in `app/settings/test.py`).
- **Theme switcher**: the staff console shows an always-visible light/dark/system control in its header (`[data-console-theme-set]`, `aria-pressed`, keyboard operable). The choice is the public site's `mobofirm-theme` key (`"light"` or `"dark"`; absent = follow the OS), applied before first paint by `theme-init.js` and resolved onto `<html data-bs-theme>` by `frontend/src/admin/js/components/theme.js`, so one choice follows the person across site and console. `tests/frontend/test_admin_console_theme_browser.py` covers the toggle and persistence.
- **Release stamp**: the sidebar foot and the page footer show the version (and build SHA when known) and the header shows the environment label, all from `app/version.py` (see `docs/08-operations/releases.md`).
- **Browser coverage**: `tests/frontend/test_admin_console_browser.py` (sign-in, every sidebar page in light and dark at every width, the dashboard-to-published post workflow), `test_admin_console_interactions.py`, `test_admin_console_theme_browser.py` and the full-URL crawl `test_admin_console_crawl.py` (Playwright; console/CSP errors, 404 assets and overflow at 1280, 768 and 360 px).
## Verification

Run the configured Vite build, focused Django/template tests, CSP/CORS/mixed-content tests, and browser/accessibility checks when the app and browser toolchain are available. Report unavailable runtime/provider checks rather than substituting static evidence.
