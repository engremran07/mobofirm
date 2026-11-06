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
- The theme is resolved before first paint by `theme-init.js` (stored choice, otherwise `prefers-color-scheme`); the toggle stores an explicit choice and storage failures are tolerated.
- Reusable partials, template helpers and shell behaviour are documented in `components.md`. Reuse them rather than re-implementing breadcrumbs, pagination, empty states, form fields, scrollable tables or confirmation dialogs.
- Only `a.surface-card` and `.surface-card--interactive` lift on hover; static cards (forms, stat tiles) do not.
- Unfilled ad slots render nothing; `.ad-placeholder` is hidden as a safety net.

## Admin console (Unfold)

The admin (`/admin/`) is `django-unfold` on a project `AdminSite` (`apps.core.admin_site.MobofirmAdminSite`, installed through `apps.core.admin_config.MobofirmAdminConfig`). The legacy overrides (`templates/admin/*` base templates, `frontend/src/admin.js`, `frontend/src/styles/admin.css`, the `admin` Vite entry and every `mf-admin-*` / `mf-review-workflow` class) were removed; `tests/backend/admin/test_admin_access.py` fails if they return.

- **Configuration** lives in `app/settings/unfold.py` (one place). The palette anchors on the public tokens (`--mf-accent`, `--mf-accent-strong`, light and dark) and `tests/backend/admin/test_admin_theme.py` fails if the two drift. Name, header, logo, dark logo and favicon come from the runtime site-settings accessor through `apps/core/admin_branding.py`.
- **Base classes** are in `apps/core/admin_base.py` (`ModelAdmin`, `NoAddModelAdmin`, `ReadOnlyModelAdmin`, `SingletonModelAdmin`, `TabularInline`, `StackedInline`, `ConfirmForm`, `ReasonForm`, `confirm_dialog`, `htmx_redirect`, `return_url`, badge maps). Every `apps/*/admin.py` imports from there, never from `unfold`/`django.contrib.admin` base classes directly.
- **Navigation**: `apps/core/admin_navigation.py` declares the sidebar once (groups: Catalog, Firmware, Tools & guides, Downloads & testing, Content, SEO, Pages, Shop, Marketplace, Files & storage, Ads, Users, Devices, Notifications, System). An entry shows only when its model is registered and the user may view or change it. A registered model that no group lists would be appended to an "Other" group; `test_admin_all_models.py`/`test_admin_access.py` fail if that group is ever non-empty. Status tabs (`TABS`) cover firmware types, comment/blog/SEO/order/listing statuses and tag state.
- **Dashboard**: `apps/core/admin_dashboard.py` computes cached aggregate counts (60 s); `templates/admin/index.html` renders only the KPI cards and tables the user has permission for. An unavailable metric renders "—", never a fabricated zero.
- **Workflows**: state changes are `@action`s. Bulk actions keep their names; row/detail actions open an Unfold dialog on GET and change state only on the dialog's CSRF-protected POST, which answers with `HX-Redirect` (blog publish/schedule/archive/request review, comment approve/reject/spam with a note, tag approve/reject/merge, device trust/block/unblock/revoke, ad scan discovery approve/reject). Services still enforce permissions; the admin only adds a UI.
- **CSP**: the Unfold shell is served under its own policy (`ADMIN_CSP` in `app/settings/security.py`, applied by `AdminContentSecurityPolicyMiddleware` to the `admin` and `seo` URL namespaces and the staff ads dashboard views listed in that module; every other page keeps the strict public policy). Scripts and styles are same-origin plus the per-request nonce (inline theme `<style>` carries it via `templates/unfold/layouts/skeleton.html`); `'unsafe-eval'` is needed only because Unfold's Alpine build compiles expressions, and `style-src-attr` allows inline style *attributes* for Alpine bindings. No third-party host is allowed. The public policy is unchanged and `test_admin_csp_is_nonce_based_and_distinct_from_the_public_policy` asserts both.
- **Browser coverage**: `tests/frontend/test_admin_unfold_browser.py` (Playwright) covers sign-in, dashboard, sidebar, changelist, change form, a bulk action, dark mode, console/CSP errors and overflow at 390, 768 and 1365 px.

## Verification

Run the configured Vite build, focused Django/template tests, CSP/CORS/mixed-content tests, and browser/accessibility checks when the app and browser toolchain are available. Report unavailable runtime/provider checks rather than substituting static evidence.
