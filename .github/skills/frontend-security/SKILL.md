---
name: frontend-security
description: "Apply and verify CSP, CORS, and mixed-content controls for Mobofirm's Django-rendered frontend and admin."
---
# Frontend Security

Use for frontend, template, CSP, CORS, or transport-security changes.

1. Read `app/settings/security.py`, the active environment settings, and the existing header tests before changing policy.
2. Keep the CSP default same-origin; do not allow inline scripts/styles, eval, or wildcard sources. Add only exact provider origins required by a verified feature.
3. Keep CORS disabled for same-origin HTMX. If cross-origin traffic is required, define the owning API, exact origins/methods/headers, credential policy, and tests before enabling it.
4. Require HTTPS for external resources and links; retain production `upgrade-insecure-requests`. Search templates and generated markup for insecure assets.
5. Keep advertising resources consent-aware. Missing/denied consent must not render a tracking creative.
6. Add regression tests for response headers, untrusted-origin behavior, mixed-content prevention, and CSP-compatible markup. Test the actual browser console when an app/browser runner is available.
7. Do not add a dependency or weaken a policy merely to silence a browser error. Report provider or deployment validation that cannot be performed locally.
