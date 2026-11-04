# Frontend Design System

## Status and approved direction

The product owner explicitly authorized implementation of the initial frontend foundation using Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide icons, and Vite. The approved architecture is hybrid: one centralized token and static-asset source shared by the public experience and admin, with distinct public/admin shells. The first-pass visual direction is light-first editorial with a balanced, multi-tone dark theme; final visual calibration and brand assets remain open for product review.

The system is not a finished visual identity. Do not claim final design approval, invent brand assets, or alter user-facing policy under this technical approval.

## Ownership

- Shared build entries, tokens, components, and behavior live in `frontend/`; built assets live under Django's configured static build directory.
- Public and admin styles remain separate entry-point layers over shared design tokens. Django admin remains the permission and persistence authority.
- App templates extend the shared public base or appropriate Django admin base; app-specific styles are namespaced extensions, never copies of shared assets.
- Do not add a competing CSS framework, a second icon library, per-app static duplicates, or an app-local build pipeline.
- Split large design files by responsibility when they exceed the repository's configured size budgets; splitting must preserve one canonical implementation.

## Current interaction and accessibility baseline

- Use semantic headings, landmarks, labels, keyboard-operable controls, visible focus, and status announcements.
- Preserve responsive behavior, reduced-motion preferences, and meaningful empty, loading, and error states.
- Keep server authorization, validation, and persisted state in Django. Alpine is limited to ephemeral interface state; HTMX responses remain server-rendered and progressively enhanced.
- Strict CSP forbids inline styles and scripts. Use centralized styles and CSP-compatible Alpine expressions.
- Same-origin requests do not require CORS. Any cross-origin resource or provider needs explicit security review and a narrow CSP allowlist.

## Verification

Run the configured Vite build, focused Django/template tests, CSP/CORS/mixed-content tests, and browser/accessibility checks when the app and browser toolchain are available. Report unavailable runtime/provider checks rather than substituting static evidence.
