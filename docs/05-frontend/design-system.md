# Frontend Design System

## Status and approved direction

The product owner explicitly authorized implementation of the initial frontend foundation using Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide icons, and Vite. The approved architecture is hybrid: one centralized token and static-asset source shared by the public experience and admin, with distinct public/admin shells. The first-pass visual direction is light-first editorial with a balanced, multi-tone dark theme; final visual calibration and brand assets remain open for product review.

The system is not a finished visual identity. Do not claim final design approval, invent brand assets, or alter user-facing policy under this technical approval.

## Ownership

- Shared build entries, tokens, components, and behavior live in `frontend/`; built assets live under Django's configured static build directory.
- The public site and the admin are separate shells. The public shell uses the Vite entries in `frontend/`; the admin is [django-unfold](https://unfoldadmin.com/) (see "Admin console (Unfold)" below) and has no Vite entry of its own. Django admin remains the permission and persistence authority.
- Public app templates extend the shared public base; admin-hosted staff pages extend `admin/base_site.html` (Unfold). App-specific styles are namespaced extensions, never copies of shared assets.
- Do not add a competing CSS framework, a second icon library, per-app static duplicates, or an app-local build pipeline.
- Split large design files by responsibility when they exceed the repository's configured size budgets; splitting must preserve one canonical implementation.

## Current interaction and accessibility baseline

- Use semantic headings, landmarks, labels, keyboard-operable controls, visible focus, and status announcements.
- Preserve responsive behavior, reduced-motion preferences, and meaningful empty, loading, and error states.
- Keep server authorization, validation, and persisted state in Django. Alpine is limited to ephemeral interface state; HTMX responses remain server-rendered and progressively enhanced.
- Strict CSP forbids inline styles and scripts. Use centralized styles and CSP-compatible Alpine expressions.
- Same-origin requests do not require CORS. Any cross-origin resource or provider needs explicit security review and a narrow CSP allowlist.

## Themes, contrast and shared components

- Light and dark token sets are each defined once in `frontend/src/styles/tokens.css`; Bootstrap's colour variables (`--bs-secondary-rgb`, `--bs-danger-rgb`, link and button variables) are mapped from them in both themes, and `tests/backend/test_frontend_contract.py` asserts WCAG AA contrast for the token pairs in use. Add a pair to that test when introducing a new foreground/background combination.
- The theme is resolved before first paint by `theme-init.js` (stored choice, otherwise `prefers-color-scheme`); the toggle stores an explicit choice and storage failures are tolerated. The admin keeps its own light/dark/system choice (`adminTheme`) and synchronises explicit choices with this `mobofirm-theme` key (see "Admin console").
- Reusable partials, template helpers and shell behaviour are documented in `components.md`. Reuse them rather than re-implementing breadcrumbs, pagination, empty states, form fields, scrollable tables or confirmation dialogs.
- Only `a.surface-card` and `.surface-card--interactive` lift on hover; static cards (forms, stat tiles) do not.
- Unfilled ad slots render nothing; `.ad-placeholder` is hidden as a safety net.

## Public design system (Wave 10)

The public shell is a premium, light-first editorial UI with a balanced multi-tone dark theme, built from CSS custom properties (the runtime theme API), SCSS partials compiled by Vite, HTMX for server interaction and Alpine's CSP-safe build for ephemeral state. It is backward compatible: Bootstrap utilities and every existing class (`surface-card`, `hero-panel`, `site-nav-link`, `btn-*`, `form-control`, …) keep working and are re-skinned by the layers below; new pages use the `*-ui` components documented here.

### Source layout and build

```
frontend/src/styles/
  tokens.css            colour themes (light, dark, high contrast) - the single colour source
  main.scss             entry (compiled to static/build/site.css), layer order below
  abstracts/            Sass-only breakpoints + mixins (motion, focus-ring, glass, hover-lift, busy-button)
  tokens/_scale.scss    type, space, radius, elevation, glass, gradient, motion, z-index tokens
  base/                 element styles, typography, selection, scrollbars, forced-colours
  legacy/_site.scss     page-level rules from the pre-SCSS stylesheet (migrate page by page)
  layout/_shell.scss    header, mega menu, footer, appearance controls, progress bar
  components/           buttons, forms, cards, badges/tabs/accordion/tooltip/toast, data (skeleton,
                        tables, pagination, filters, stepper), overlays (hero, command palette,
                        sheet), dialog
  utilities/            small helpers (stack, cluster, glass, elev-*)
  themes/               per-theme refinements and the reduced-motion switch
```

Layer order is tokens → base → legacy → layout → components → utilities → themes, imported by `main.scss`; Bootstrap's minified CSS is imported first in `site.js`, feature stylesheets (`blog.css`, `ecosystem.css`, …) after. Output names in `static/build/` are unchanged (`site.css`, `site.js`, `tokens.css`, `theme-init.js`, admin and SEO files). `tokens.css` stays plain CSS on purpose: Python tests and the admin theme read its hex values. Run `npm run build` after any change (do not run two builds at once: `emptyOutDir` makes concurrent builds clash).

### Tokens

| Group | Tokens |
| --- | --- |
| Colour (`tokens.css`) | `--mf-page/surface/surface-raised/surface-soft/border/text/text-muted/accent/accent-strong/accent-ink/accent-warm/accent-cool/success/warning/danger/info/focus`, `--mf-glass-bg(-strong)/glass-border`, `--mf-overlay`, `--mf-skeleton-base/shine`, `--mf-shadow-rgb` |
| Type (`_scale.scss`) | fluid `--mf-text-2xs … --mf-text-5xl` (clamp, 360→1440 px), `--mf-leading-*`, `--mf-tracking-*`, `--mf-font-sans/mono` |
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
- **Empty state / stat tile / avatar / rank / section header**: `.empty-ui`, `.stat-ui`, `.avatar-ui`, `.rank-ui`, `.section-ui` (the existing `empty_state.html` and `stat_tile.html` partials are re-skinned).
- **Tables**: `.table-scroll` / `.table-sticky` (+ `.table-ui`) give a rounded scroller; `.table-sticky` keeps the header visible.
- **Pagination and infinite scroll**: `components/pagination.html` (pill pagination, HTMX-aware). Infinite scroll: `components/load_more.html` renders a sentinel with `hx-trigger="revealed"` and a "Load more" link fallback; the server returns the next rows plus a new sentinel.
- **Filters**: `.filter-bar` (sticky glass bar), `.chip-ui` (`aria-pressed`/`.is-active`, `.chip-ui__remove`), `.chip-row`.
- **Sheets and dialogs**: `<dialog class="sheet-ui">` is a modal on desktop and a bottom sheet on phones; app dialogs use the shared shell (`dialogs.md`).
- **Stepper / breadcrumbs / hero**: `.stepper-ui` (`.is-done`, `.is-current`), `.breadcrumbs` (existing partial), `.hero-ui` / `.hero-panel` with `.hero-ui__title`, `.text-gradient`, `.hero-ui__actions/stats`.
- **Scroll reveal**: `data-reveal` (staggered, once). **Copy to clipboard**: `data-copy="text"`, `data-copy-target="#id"` or `data-copy-url`. **Optimistic UI**: `data-optimistic` (+ `data-optimistic-toggle="aria-pressed"`) flips immediately and reverts on failure. **View transitions**: opt in per swap with `hx-swap="innerHTML transition:true"` (plain swap where unsupported; disabled under reduced motion).
- **Footer**: three-column grid, version stamp, appearance controls.

### Verification

`tests/backend/test_frontend_contract.py` (tokens/contrast incl. high contrast, shell and partial contracts), `tests/frontend/test_design_system_browser.py` (tokens, theme persistence, palette, mega menu, toasts, loading, reduced motion, components, no console/CSP errors, 360/768/1440 overflow), `tests/frontend/test_public_shell_browser.py` and `tests/frontend/test_dialogs_browser.py`.

## Admin console (Unfold)

The admin (`/admin/`) is `django-unfold` on a project `AdminSite` (`apps.core.admin_site.MobofirmAdminSite`, installed through `apps.core.admin_config.MobofirmAdminConfig`). The legacy overrides (`templates/admin/*` base templates, `frontend/src/admin.js`, `frontend/src/styles/admin.css`, the `admin` Vite entry and every `mf-admin-*` / `mf-review-workflow` class) were removed; `tests/backend/admin/test_admin_access.py` fails if they return.

- **Configuration** lives in `app/settings/unfold.py` (one place). The palette anchors on the public tokens (`--mf-accent`, `--mf-accent-strong`, light and dark) and `tests/backend/admin/test_admin_theme.py` fails if the two drift. Name, header, logo, dark logo and favicon come from the runtime site-settings accessor through `apps/core/admin_branding.py`.
- **Base classes** are in `apps/core/admin_base.py` (`ModelAdmin`, `NoAddModelAdmin`, `ReadOnlyModelAdmin`, `SingletonModelAdmin`, `TabularInline`, `StackedInline`, `ConfirmForm`, `ReasonForm`, `confirm_dialog`, `htmx_redirect`, `return_url`, badge maps). Every `apps/*/admin.py` imports from there, never from `unfold`/`django.contrib.admin` base classes directly.
- **Navigation**: `apps/core/admin_navigation.py` declares the sidebar once (groups: Catalog, Firmware, Tools & guides, Downloads & testing, Content, SEO, Pages, Shop, Marketplace, Files & storage, Ads, Users, Devices, Notifications, System). An entry shows only when its model is registered and the user may view or change it. A registered model that no group lists would be appended to an "Other" group; `test_admin_all_models.py`/`test_admin_access.py` fail if that group is ever non-empty. Status tabs (`TABS`) cover firmware types, comment/blog/SEO/order/listing statuses and tag state.
- **Dashboard**: `apps/core/admin_dashboard.py` computes cached aggregate counts (60 s); `templates/admin/index.html` renders only the KPI cards and tables the user has permission for. An unavailable metric renders "—", never a fabricated zero.
- **Workflows**: state changes are `@action`s. Bulk actions keep their names; row/detail actions open an Unfold dialog on GET and change state only on the dialog's CSRF-protected POST, which answers with `HX-Redirect` (blog publish/schedule/archive/request review, comment approve/reject/spam with a note, tag approve/reject/merge, device trust/block/unblock/revoke, ad scan discovery approve/reject). Services still enforce permissions; the admin only adds a UI.
- **CSP**: the Unfold shell is served under its own policy (`ADMIN_CSP` in `app/settings/security.py`, applied by `AdminContentSecurityPolicyMiddleware` to the `admin` and `seo` URL namespaces and the staff ads dashboard views listed in that module; every other page keeps the strict public policy). Scripts and styles are same-origin plus the per-request nonce (inline theme `<style>` carries it via `templates/unfold/layouts/skeleton.html`); `'unsafe-eval'` is needed only because Unfold's Alpine build compiles expressions, and `style-src-attr` allows inline style *attributes* for Alpine bindings. No third-party host is allowed. The public policy is unchanged and `test_admin_csp_is_nonce_based_and_distinct_from_the_public_policy` asserts both.
- **Theme switcher**: Unfold only offers light/dark/system inside the account menu at the bottom of the sidebar (hidden on narrow screens), so the project overrides `unfold/helpers/userlinks.html` to show an always-visible three-button group (`templates/unfold/helpers/theme_toggle.html`, `aria-pressed`, keyboard operable) in the header, next to the version stamp and environment badge. The choice is Unfold's persisted `adminTheme` (localStorage key `adminTheme`: `"light"`, `"dark"` or `"auto"`, default `"auto"` = follow the OS). `frontend/src/admin-theme.js` (a same-origin classic script registered through `UNFOLD["SCRIPTS"]`, no inline script) applies the stored choice before first paint, adopts the public site's explicit `mobofirm-theme` (`dark`/`light`) while the admin choice is unset or `auto`, and mirrors an explicit admin choice back to `mobofirm-theme` (choosing System removes it). Styles: `frontend/src/styles/admin-theme.css` via `UNFOLD["STYLES"]`. `tests/frontend/test_admin_theme_browser.py` covers toggle, persistence, first paint, sync, keyboard and a 390 px layout.
- **Release stamp**: the header, dashboard strip and sidebar branding show the version, build SHA and environment label from `app/version.py` (see `docs/08-operations/releases.md`).
- **Browser coverage**: `tests/frontend/test_admin_unfold_browser.py` (Playwright) covers sign-in, dashboard, sidebar, changelist, change form, a bulk action, dark mode, console/CSP errors and overflow at 390, 768 and 1365 px.

## Verification

Run the configured Vite build, focused Django/template tests, CSP/CORS/mixed-content tests, and browser/accessibility checks when the app and browser toolchain are available. Report unavailable runtime/provider checks rather than substituting static evidence.
