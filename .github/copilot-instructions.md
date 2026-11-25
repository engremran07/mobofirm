# Mobofirm Copilot Instructions

Follow [AGENTS.md](../AGENTS.md), the scoped instructions in `.github/instructions/`,
and the approved architecture and governance documents. Treat source, settings,
migrations, and tests as evidence of implemented behavior; do not promote proposal
text or sample configurations into product requirements.

## Verified project shape

- Django configuration is under `app/`; the installed local apps are under `apps/`.
  Verify `app/settings/base.py` and `docs/04-backend/django-app-boundaries.md`
  before assuming an app, provider, or integration exists.
- Run Python and Django commands through `.venv\Scripts\python.exe` on Windows.
  The test settings use in-memory SQLite; PostgreSQL and Redis are runtime
  infrastructure and must not be accessed for routine tests.
- Keep each domain app's data and rules in its owning app. Follow documented
  contracts; do not add direct sibling-app imports or invent a generic event bus.
- Keep the public site and staff console on their approved, distinct frontend
  shells. Preserve CSP, CSRF, consent, accessibility, and existing offline assets.

## Security and external services

- Never place secrets, database credentials, tokens, private URLs, or production
  data in source, instructions, hook output, or MCP configuration. Reference local
  environment variables instead.
- MCP server configuration is executable integration configuration, not a safety
  boundary. Review publisher, tools, scopes, and target before enabling a server.
  Keep payment, production observability, database, and browser profiles separate;
  use only local/test resources unless task-specific approval says otherwise.
- Use `apps.seo.fetcher.SafeFetcher` for outbound application HTTP and explicit
  host allow-lists. Never store raw client IP or User-Agent in analytics records.
- Never run migrations, cleanup, destructive SQL, or data repair against a
  non-test database without explicit authorization and target verification.

## Do not infer unimplemented product scope

- Online payment processing is disabled. `shop` orders are staff-fulfilled, and
  wallet top-ups/payouts are offline staff decisions. Whop, Stripe, and PayPal MCP
  profiles are optional research/test tools, not evidence that these rails are
  implemented or approved for production.
- Provider-backed AI code is not part of approved product scope; its legacy
  client is removed in the current worktree. Firmware-to-blog posts are generated
  deterministically from approved catalog data and remain supported. SEO's
  `robots.txt` controls for AI crawlers are an SEO policy, not an AI provider
  integration, and remain. Do not add a provider integration or change retained
  automation, crawler policy, or historical content without explicit approval.
- Distribution currently uses the API versions and adapters present in
  `apps/distribution/`. Verify current official provider documentation before
  proposing an API-version change; do not copy version numbers from an example.

## Verification

Add focused tests for behavior changes in root `tests/backend/` or
`tests/frontend/`. Run applicable checks using the repository environment and
report exact commands, results, and anything unavailable. Frontend behavior,
template, or layout changes require Playwright coverage and the applicable Vite
build.
