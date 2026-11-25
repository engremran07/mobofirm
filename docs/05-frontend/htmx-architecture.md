# Django, HTMX, and Alpine Architecture

## Status

The public site and staff console use the shared htmx 4.0.2 runtime from `htmx.org`.
Their presentation shells remain distinct; console-only boost behavior stays in
`frontend/src/admin/`. Alpine uses `@alpinejs/csp`; expressions must remain compatible
with that CSP-safe runtime.

The shared runtime disables implicit attribute inheritance, generated indicator CSS,
and htmx expression execution. It rejects `hx-on*` binding and strips executable scripts
and inline event-handler attributes from every swap. The public and admin bundles import
the same runtime configuration rather than carrying separate htmx copies.

## Interaction contracts

- Django views remain the authority for authentication, permissions, validation, and persisted business state.
- HTMX is for server-rendered interactions and fragments. Provide a standard form/link fallback for navigation and state changes wherever practical.
- An HTMX request returns the expected fragment, with explicit empty and invalid states. Do not return success-shaped markup for authorization or server failures.
- Keep CSRF enabled for every state-changing request. Preserve the shared `X-CSRFToken` header and include a CSRF form token where normal form submission is supported.
- Alpine is limited to local ephemeral UI state such as the mobile menu. It must not duplicate server-owned records or policies.
- Keep fragments idempotent and avoid event-handler accumulation. Reinitialize only behaviors that require it after a swap.
- Provide loading/error feedback, accessible live regions, keyboard operation, and sensible focus behavior.
- Loading: the public body carries inherited `hx-indicator` and `hx-headers` values; use local indicators for inline feedback. The console has its own progress treatment. HTMX failures use htmx 4 lifecycle events and render visible, dismissible feedback.
- Triggers: use the htmx 4 trigger syntax, e.g. `input changed delay:250ms` and `from:#search-query`; do not rely on htmx 2 selector extensions or camelCase event names.
- Global search: the header combobox calls `firmwares:api_search_autocomplete` through HTMX; `modules/search.js` turns the JSON into DOM nodes (no server HTML is inserted) and the plain GET form falls back to `firmwares:universal_search`.

## Security and operations

- CSP disallows inline scripts, inline styles, and eval in the frontend. The shared htmx configuration blocks expression evaluation; do not use `hx-on`, `js:` values, or bracket trigger filters. Do not use `x-show`, inline CSS, or `x-html` with untrusted content.
- Executable scripts and inline event handlers are removed from htmx swap fragments. Do not depend on scripts embedded in fragments for initialization.
- CORS remains disabled for same-origin application flows. Do not add a wildcard or broad origin to make HTMX work.
- Production HTTPS and `upgrade-insecure-requests` protect transport; use HTTPS-only external links and assets.
- Third-party ad/provider code is not implicitly trusted by enabling HTMX or loosening CSP. Provider origins and consent behavior require an explicit narrow policy.

## Verification

Test a normal request and its HTMX fragment contract, server-side authorization, CSRF, invalid input, and empty states. Run the Vite builds and inspect the actual browser console/CSP report when browser access is available.
