---
name: frontend-stack
description: "Build and verify Django-rendered frontend work involving HTML, HTMX, Alpine.js, Sass/SCSS, CSS, Tailwind CSS, or Lucide icons; first detect which technologies this repository actually uses."
---
# Frontend Stack

Use for frontend changes in this repository. The user-approved stack is Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide, and Vite. Verify `package.json`, the build entries, and the actual templates before changing the stack; do not introduce a second framework or styling system.

1. Inventory templates/static files and actual package/build manifests. Select only the requested, already configured stack; ask before adding a framework, compiler, or dependency.
2. Keep Django as the authorization and persistence authority. Escape output, retain CSRF protection, validate server-side, and make HTMX responses and errors explicit. A static gallery is supplementary only; when the user asks for live ad placement or flow testing, exercise the actual renderer and state transitions with safe, isolated demo data.
3. Use HTMX for server-rendered partial interactions and Alpine for small local UI state when present. Avoid duplicating business state, unsafe HTML insertion, and event-handler accumulation on fragment swaps.
4. Use existing Sass/CSS or Tailwind conventions and tokens; do not mix styling systems or generate dynamic utility classes the configured build cannot discover.
5. Use existing Lucide integration and accessible labels. Decorative icons are hidden from assistive technology; icon-only controls get an accessible name. Do not install an icon package just to render a trivial symbol.
6. Verify keyboard and screen-reader semantics, focus after swaps/dialog changes, responsive layouts, reduced motion, and loading/empty/error states.
7. Run the Vite build, focused Django tests using the repository `.venv` Python interpreter, and Playwright route/responsive/interaction checks. Frontend browser checks are required; do not silently skip missing browser setup or fabricate screenshots or emulator evidence.
8. For forms, retain the centralized Django CSRF middleware and template tokens; verify the app-owned CSRF failure page, accessible search/validation states, and POST-Redirect-GET behavior. Do not use browser-native alert/confirm/prompt dialogs.
9. Do not silently reduce a requested format or provider scope to the currently implemented subset. Separate creative formats, placements, delivery/buying methods, and reward verification; research standards and ask the user to select platforms/providers when scope is open-ended.

Use HTMX for the server-rendered interactions that benefit from partial updates and Alpine for bounded local state; do not treat "use every feature" as a requirement to activate unrelated or CSP-incompatible capabilities. On changed routes, verify console errors/warnings and horizontal overflow at narrow and desktop viewports, and report any pre-existing global diagnostics separately.
