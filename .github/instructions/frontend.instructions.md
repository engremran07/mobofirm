---
description: "Use when changing Django templates or frontend HTML, HTMX, Alpine.js, CSS, SCSS, Tailwind, or Lucide icon integration."
applyTo: ["**/*.html", "**/*.htm", "**/*.css", "**/*.scss", "**/*.js"]
---
# Frontend

- First identify which frontend libraries and build tools are actually installed and used. Do not mix in HTMX, Alpine, Sass, Tailwind, or Lucide unless the project already uses them or the task explicitly authorizes adding them.
- Keep Django templates accessible, semantic, escaped, and progressively enhanced. Preserve CSRF protection and validate every state-changing request server-side.
- Give HTMX partials stable response semantics; use Alpine only for local UI state, not as a second source of persisted business truth.
- Follow the existing CSS strategy and design tokens. Avoid generated utility-class names, duplicated design systems, and new icon dependencies when inline or existing project icons suffice.
- Verify keyboard/focus behavior, reduced motion, narrow viewports, and error/loading/empty states. Run configured frontend checks and browser tests when available; do not claim a browser/emulator test ran when its app or runner is absent.
