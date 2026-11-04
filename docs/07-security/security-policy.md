# Security Policy

## Baseline

- Keep secrets in environment/deployment secret storage; `.env.example` contains placeholders only.
- Use Django authentication, server-side permission checks, CSRF protection for state-changing browser requests, and object ownership checks.
- Validate all client-controlled values at the boundary. Treat device payloads, user-agent strings, consent flags, affiliate parameters, uploaded firmware metadata, and file identifiers as untrusted.
- Keep firmware file access behind the existing authorization/token/signature checks; do not expose local storage paths.
- Do not log passwords, tokens, private keys, provider credentials, raw request bodies, or unnecessary personal/device data.
- Review every dependency addition and keep versions compatible with the supported Python/Django range.
- Use production-only HTTPS/cookie/HSTS settings from the production settings module, after verifying the deployment proxy and host configuration.
- Keep the browser Content Security Policy restrictive: same-origin defaults, no inline scripts, no eval, no wildcard sources, and explicit provider origins only when required and reviewed. Inline styles remain blocked except for the exact fixed Pygments stylesheet hash emitted by Django REST framework's browsable API.
- Keep CORS disabled for same-origin Django/HTMX interactions. Add an exact trusted origin only for a documented cross-origin contract; never use wildcard origins with credentials.
- Prevent mixed content: deploy over HTTPS, use HTTPS-only external resources, retain production `upgrade-insecure-requests`, and test template assets for `http://` URLs.
- Ad rendering must honor the user's advertising-consent category. A denied or missing consent renders only a non-tracking placeholder.

## Reporting and remediation

Security findings must include affected path, conditions, impact, confidence, and a safe reproduction that uses only authorized local/test data. Critical and high findings block release until fixed or explicitly accepted by the security owner with a documented compensating control.

## Scope limits

This baseline is not a completed penetration test, dependency CVE assessment, privacy review, or deployment hardening certification. External identity providers, storage, email, Redis, and PostgreSQL production configuration require environment-specific review.

## Browser security headers

The frontend regression suite verifies CSP directives, the exact approved DRF stylesheet hash, the absence of unauthorized CORS grants, production mixed-content protection, and the absence of inline styles in project templates and insecure template asset URLs. Browser route checks also fail on CSP violations. Any policy change must update focused tests and be confirmed in a real browser when available; provider allowlists must remain narrow and consent-aware.
