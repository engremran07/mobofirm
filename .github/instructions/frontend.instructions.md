---
description: "Use when changing Django templates or frontend HTML, HTMX, Alpine.js, CSS, SCSS, Tailwind, or Lucide icon integration."
applyTo: ["**/*.html", "**/*.htm", "**/*.css", "**/*.scss", "**/*.js"]
---
# Frontend

- First identify which frontend libraries and build tools are actually installed and used. Do not mix in HTMX, Alpine, Sass, Tailwind, or Lucide unless the project already uses them or the task explicitly authorizes adding them.
- Keep Django templates accessible, semantic, escaped, and progressively enhanced. Preserve CSRF protection and validate every state-changing request server-side.
- Include `{% csrf_token %}` in every HTML POST form. Do not replace Django CSRF middleware with custom token code, read an HttpOnly CSRF cookie from JavaScript, or use `csrf_exempt` for browser/session writes.
- Do not use browser-native `alert()`, `confirm()`, or `prompt()` for product confirmations. Use accessible app-owned UI when confirmation is required, and use POST-Redirect-GET after successful browser writes to prevent resubmission prompts.
- Give HTMX partials stable response semantics; use Alpine only for local UI state, not as a second source of persisted business truth.
- Use only the HTMX and Alpine behavior a flow needs; do not enable every framework feature indiscriminately. Keep HTMX evaluation/script execution disabled under the current CSP, and preserve a working non-JavaScript path for catalog search and filters.
- Preserve the configured strict CSP: avoid inline scripts/styles, eval-dependent interactions, `x-show`, `hx-on`, and unsafe HTML insertion. Same-origin HTMX does not need CORS.
- Use HTTPS for external assets and links. Any new third-party origin requires a narrow CSP review and documented consent/data implications.
- Follow the existing CSS strategy and design tokens. Avoid generated utility-class names, duplicated design systems, and new icon dependencies when inline or existing project icons suffice.
- Verify keyboard/focus behavior, reduced motion, narrow viewports, horizontal overflow, and error/loading/empty states. Run the Vite build and Playwright route/interaction checks for frontend changes; browser coverage is required, not silently skipped.
- Inspect browser console errors and warnings on changed routes. Resolve newly introduced diagnostics; do not hide pre-existing or out-of-scope messages merely to make the console appear clean.
- Run Django and Python checks with the repository `.venv` interpreter (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/macOS).
- Do not treat a static gallery or mocked placeholder as completion of a requested live feature. Use the actual application rendering and authorization path for functional previews; clearly separate inert visual samples from provider-backed behavior, and ask before omitting requested formats or flows.
