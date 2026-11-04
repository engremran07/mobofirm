---
name: secure-form-workflow
description: "Use when implementing or auditing Django forms, CSRF, email verification, authentication, logout/password-reset controls, browser confirmations, or POST resubmission."
---

# Secure Form Workflow

Use the existing Django server-rendered stack and keep the server authoritative for every state change.

## Procedure

1. Trace the URL, view, middleware, form, template, session, and side-effect path. Confirm `CsrfViewMiddleware` remains enabled in `app/settings/base.py`.
2. Keep global Django CSRF verification as the single authority. Every rendered HTML POST form includes `{% csrf_token %}`. Do not add `csrf_exempt` to browser/session writes or implement a second CSRF token system.
3. Keep DRF session-authenticated writes subject to DRF's CSRF enforcement. Treat token-authenticated API requests as a separate explicit contract; do not make a browser cookie an implicit API credential.
4. Route CSRF failures to an app-owned, accessible 403 page without exposing tokens or request bodies. Never accept a missing or incorrect token as a success.
5. Before creating an authenticated session or performing account/device/workflow side effects, require the account's email verification state. Send the user through the app's verification flow when it is incomplete.
6. Make successful browser POST handlers redirect to a safe GET. Do not mutate state on GET. Use messages or safe form state for feedback; never store passwords or one-time codes in redirect state.
7. Do not use JavaScript `alert()`, `confirm()`, or `prompt()`. If a destructive action truly needs confirmation, use an accessible application-owned modal with keyboard/focus handling and a server-protected POST.
8. Keep logout and password-reset actions reachable from the account UI. Logout uses a CSRF-protected POST.
9. For country inputs, validate choices server-side, search local option labels (country name, ISO code, calling code), show detected location as an editable suggestion, and do not persist detection merely because a GET page was viewed.
10. Add tests for missing/invalid and valid CSRF tokens, unverified login and side-effect rejection, redirect-after-write behavior, account entry points, and responsive/browser interactions. Use `.venv\Scripts\python.exe` on Windows and the configured Playwright runner.

## Completion

Run the focused Django tests, relevant app/integration tests, Django checks, Vite build for frontend edits, and Playwright coverage. Report browser setup or external geolocation limitations explicitly. Never weaken CSRF or email verification to make tests pass.
