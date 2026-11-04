# Django, HTMX, and Alpine Architecture

## Status

HTMX and Alpine are part of the user-approved frontend foundation. The current build imports HTMX as an ES module, exposes its instance to `window.htmx`, and disables eval- and script-tag processing. Alpine uses `@alpinejs/csp`; expressions must remain compatible with that CSP-safe runtime.

## Interaction contracts

- Django views remain the authority for authentication, permissions, validation, and persisted business state.
- HTMX is for server-rendered interactions and fragments. Provide a standard form/link fallback for navigation and state changes wherever practical.
- An HTMX request returns the expected fragment, with explicit empty and invalid states. Do not return success-shaped markup for authorization or server failures.
- Keep CSRF enabled for every state-changing request. Preserve the shared `X-CSRFToken` header and include a CSRF form token where normal form submission is supported.
- Alpine is limited to local ephemeral UI state such as the mobile menu. It must not duplicate server-owned records or policies.
- Keep fragments idempotent and avoid event-handler accumulation. Reinitialize only behaviors that require it after a swap.
- Provide loading/error feedback, accessible live regions, keyboard operation, and sensible focus behavior.

## Security and operations

- CSP disallows inline scripts, inline styles, and eval in the frontend. The only approved style hash is for the fixed DRF browsable-API syntax-highlighting stylesheet. Do not use inline event handlers, `x-show`, inline CSS, `hx-on`, or `x-html` with untrusted content.
- CORS remains disabled for same-origin application flows. Do not add a wildcard or broad origin to make HTMX work.
- Production HTTPS and `upgrade-insecure-requests` protect transport; use HTTPS-only external links and assets.
- Third-party ad/provider code is not implicitly trusted by enabling HTMX or loosening CSP. Provider origins and consent behavior require an explicit narrow policy.

## Verification

Test a normal request and its HTMX fragment contract, server-side authorization, CSRF, invalid input, and empty states. Run the Vite build and inspect the actual browser console/CSP report when browser access is available.
