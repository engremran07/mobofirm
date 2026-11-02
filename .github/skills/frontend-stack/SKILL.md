---
name: frontend-stack
description: "Build and verify Django-rendered frontend work involving HTML, HTMX, Alpine.js, Sass/SCSS, CSS, Tailwind CSS, or Lucide icons; first detect which technologies this repository actually uses."
---
# Frontend Stack

Use for frontend changes in this repository. This checkout currently has no frontend source or build manifest: do not claim these tools are installed or introduce them by assumption.

1. Inventory templates/static files and actual package/build manifests. Select only the requested, already configured stack; ask before adding a framework, compiler, or dependency.
2. Keep Django as the authorization and persistence authority. Escape output, retain CSRF protection, validate server-side, and make HTMX responses and errors explicit.
3. Use HTMX for server-rendered partial interactions and Alpine for small local UI state when present. Avoid duplicating business state, unsafe HTML insertion, and event-handler accumulation on fragment swaps.
4. Use existing Sass/CSS or Tailwind conventions and tokens; do not mix styling systems or generate dynamic utility classes the configured build cannot discover.
5. Use existing Lucide integration and accessible labels. Decorative icons are hidden from assistive technology; icon-only controls get an accessible name. Do not install an icon package just to render a trivial symbol.
6. Verify keyboard and screen-reader semantics, focus after swaps/dialog changes, responsive layouts, reduced motion, and loading/empty/error states.
7. Run configured format/build/type checks and focused browser tests. If the app or browser runner is unavailable, state that explicitly; do not fabricate screenshots or emulator evidence.
