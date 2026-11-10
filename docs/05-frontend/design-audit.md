# Public frontend design audit (Wave 10)

Scope: `frontend/src/**`, `vite.config.js`, `package.json`, `templates/base.html`, `templates/components/**`, `templates/includes/**`, `docs/05-frontend/**`. Evidence is the source at the start of Wave 10 (baseline `static/build`: `site.css` 279,760 B / 39,965 B gzip, `site.js` 187,705 B / 56,140 B gzip).

## What existed

- **Stack**: Django templates, Bootstrap 5.3 (full minified CSS), HTMX 2, Alpine CSP build, Lucide (≈85 registered icons), Vite. Strict CSP (`style-src 'self'`, no inline styles or scripts). One colour-token file (`tokens.css`, light + dark) shared with the admin theme and read by Python tests.
- **Shell**: sticky header with brand, shared combobox search, theme toggle, account actions; one row of plain nav links; mobile panel; simple footer; skip link; HTMX top progress bar; a single `#global-feedback` alert for HTMX failures; Django messages as Bootstrap alerts.
- **Dialogs**: a complete native `<dialog>` shell (`components/dialog.html`, `appDialog`, `Dialogs` API, unsaved guard, focus trap/restore, ESC/backdrop rules, bottom sheet under 576 px).
- **Components**: breadcrumbs, pagination, empty state, form field, stat tile, meter, notice, status pill, tag badges, combobox, request CTA (see `components.md`).
- **Styles**: one 800-line `site.css` of mixed shell, page and component rules, plus feature stylesheets (`blog`, `ecosystem`, `marketplace`, `catalog`, `community`, `promotions`, `rewarded`, `ads`, `dialogs`, `combobox`).

## What was basic

- No spacing, type, radius, elevation, glass, gradient or motion tokens; sizes were literals, type was Bootstrap's static scale.
- No SCSS: no mixins, no partials, no layering; `site.css` mixed concerns and was hard to extend safely.
- Header and footer were functional but flat: one nav row, no menus, no command palette, no appearance controls, no scroll state.
- Buttons, inputs and cards were stock Bootstrap with token colours only: no depth, no focus glow, no loading state.
- Feedback was one dismissible alert; Django messages and HTMX errors looked different and neither stacked, timed out or paused.
- Loading was a 3 px bar only: no skeletons, no per-element busy state, no optimistic helper.
- Theme: light/dark only (no explicit "auto", no high-contrast option, no cross-tab sync).
- No tabs, accordion, tooltip, popover, chip/filter bar, stepper, sticky-header table, infinite-scroll or load-more pattern, no scroll reveal, copy-to-clipboard or view-transition helper.
- Cards had a single generic look; no firmware/model/brand/chipset/article/package/bounty variants, badges (free/featured/paid/verified/popular) or skeletons.

## Gaps closed in this wave

| Gap | Delivered |
| --- | --- |
| No SCSS | `sass` added as a dev dependency; layered SCSS (`main.scss`) compiled by Vite; stable output names |
| No design tokens | fluid type, 4 px spacing, radius, elevation, glass, gradient, motion and z-index tokens; high-contrast colour block; accent-derived gradients |
| Flat header/footer | glass sticky header with scroll state, mega menu, command palette (Ctrl/Cmd+K, shared combobox), footer redesign with appearance controls |
| Theme | auto/light/dark + high contrast + cross-tab sync + reduced-motion switch |
| Feedback | toast system (Django messages, HTMX failures, server `HX-Trigger`, JS API) |
| Loading | top bar, `[data-loading]` element state, skeleton host/cards, busy buttons |
| Components | cards, badges, tabs, accordion, tooltip/popover, chips, sticky filter bar, stepper, stat tile, avatar/rank, sticky table, pill pagination, load-more sentinel, hero, bottom sheet, form layout |
| Helpers | scroll reveal, copy to clipboard, optimistic UI, opt-in view transitions |
| Dialogs | scroll edge shadows, loading state, double-submit guard, focus first invalid field, in-place success toast, `[data-dialog-close]` and trap/restore for hand-written dialogs (see `dialog-audit.md`) |

## Still open (later waves)

- Page templates still use legacy classes (`surface-card`, Bootstrap grid/utilities); they inherit the new skin but should migrate to `card-ui`, `section-ui`, `form-layout` and the new badges page by page.
- `legacy/_site.scss` and the feature stylesheets (`blog.css`, `ecosystem.css`, `marketplace.css`, …) are plain CSS/SCSS awaiting migration into the component layer.
- Bootstrap's full CSS (≈230 KB raw) is still bundled because pages depend on its grid, utilities and component classes; trimming it needs a per-page migration first.
- Visual calibration remains a product/design review item (the approved direction is first-pass).
- A staff-only `/design/` showcase was not added (it needs a route owned by another area); the component snippets live in `design-system.md` and are exercised by `tests/frontend/test_design_system_browser.py`.
