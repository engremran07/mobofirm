# Engineering Workflow

This checkout is a runnable Django project. Django project configuration is in `app/`; the installed application packages are siblings under `apps/`.

## Installed applications

Verify `app/settings/base.py` for the live inventory. The 21 installed local apps are `users`, `core`, `ads`, `devices`, `blog`, `comments`, `firmwares`, `tags`, `storage`, `seo`, `pages`, `shop`, `marketplace`, `wallet`, `referral`, `consent`, `gamification`, `bounty`, `distribution`, `promotions`, and `site_settings`. Profile, following, activity, and the "My contributions" hub (formerly "My files") are owned by `users`. Do not add dependencies on apps that are not installed.

## Workspace workflows

Repo-local skills are available under `.github/skills/`:

- `django-feature-loop` — implement a feature or regression end to end.
- `django-migration-audit` — inspect migration safety and schema drift.
- `inter-app-automation` — trace workflows crossing app boundaries.
- `frontend-stack` — inspect the configured frontend before any approved frontend work.
- `distribution-adapter` — implement a platform adapter within the distribution contracts.
- `payment-rail-integration` — assess an explicitly approved payment rail; current online payment processing is disabled.
- `security-boundary-audit` — review security, privacy, provider, and AI boundaries.
- `observability-retention` — handle structured logs, telemetry, and safe retention.
- `deep-verification` — escalate verification and diagnose difficult regressions.
- `secure-form-workflow` — preserve global CSRF checks, email-verification gates, app-owned confirmations, and POST-Redirect-GET.
- `release-management` — prepare SemVer releases, maintain `CHANGELOG.md`, and validate tagged GitHub releases.

Agent definitions are in `.github/agents/`; repository-wide operating rules are in `AGENTS.md`. Agent and skill indexes are in `.agents/registry.yaml`.

### Copilot workspace profiles

The root `.mcp.json` is the Core profile for configured remote documentation and
GitHub services (maximum five servers). Local Serena, PostgreSQL, and Redis servers
are kept in `.mcp-profiles/local-infrastructure.json` because they require `uvx` and
explicitly configured local/test read-only connections. Before activating that
profile, confirm the PostgreSQL role grants and Redis ACL are actually read-only;
never point either server at production without task-specific approval. MCP tool
allow-lists do not replace least-privilege service accounts.

Optional profile files in `.mcp-profiles/` are not auto-loaded by Copilot. To switch,
save the current `.mcp.json` and deliberately replace it with one profile file:

- `frontend.json`: Context7, Chrome DevTools, and Playwright; use an isolated local app.
- `local-infrastructure.json`: Serena, PostgreSQL, and Redis; requires `uvx` plus
  verified local/test read-only service accounts.
- `payments.json`: Whop, Stripe, and PayPal Sandbox; these are not a signal that
  payment rails are implemented. The current shop/wallet workflows are offline.
- `operations.json`: Sentry; use only after production observability access is
  explicitly approved.

Only one profile should be active per session. The proposed `semgrep/mcp` project is
archived, and no trustworthy official Google Search Console MCP was verified; neither
is configured. The Semgrep VS Code extension is only a recommendation and is not an
MCP server.

Workspace hooks are in `.github/hooks/hooks.json`. They report verified workspace
facts, deny selected destructive commands and edits to secrets/hook scripts, and
report Ruff issues after Python file edits. They are defense-in-depth, not a
security sandbox; inspect their output and retain normal tool approval.

## Feature-to-evidence loop

For each feature or defect, record its user-observable behavior, owning app, approval state, affected integration boundaries, and a focused regression test. Add contract/integration coverage when an event, task, service, model, or API crosses app ownership. Include permission, invalid input, duplicate/retry, and failure cases where applicable. A test must assert the required outcome, not merely exercise the code.

Implement at the existing owning layer, keep state authoritative in one place, and preserve transaction semantics. Review generated migrations for reversibility and data/locking impact. Run the focused test first, then the broader relevant suite and configured static/runtime checks. Do not report unavailable commands as passing.

## Application inventory

Run `.venv\Scripts\python.exe scripts\application_map.py --output $env:TEMP\application-map.json` on Windows or `.venv/bin/python scripts/application_map.py --output /tmp/application-map.json` on Linux/macOS before a broad audit. The deterministic JSON index records installed local apps/models, resolved Django URL callbacks, template inheritance/includes, observed HTMX/Alpine attributes, static and `frontend/src/` sources, and app-owned/integration test files. CI publishes the generated map as a short-lived artifact.

This is a static discovery aid, not a feature specification, call graph, coverage guarantee, or proof of browser behavior. It cannot infer intended product capabilities, authorization correctness, interactions after an HTMX DOM swap, or whether a visible control succeeds. Treat map relationships as candidates, then trace source and exercise the real journey in Playwright. Keep approved requirements and acceptance evidence in the product/quality sources rather than generated output.

## Evidence-based agent engineering

The reliable unit of completion is an approved behavior linked to implementation and observed evidence: a product acceptance criterion, the owning backend/data boundary, the rendered UI state, and a test of the user-visible outcome. Backend, template, and browser tests answer different questions; passing one layer does not establish feature completeness. A generated map supports discovery, while the feature catalog and requirement-to-test traceability remain the gap register.

For HTML-over-the-wire flows, keep Django authoritative for validation and authorization, HTMX responsible for requests/swaps, and Alpine limited to transient browser state. Test request, response, swapped DOM, post-swap behavior, failure, and overlapping requests where the journey uses them. Prefer isolated browser tests that interact through accessible user-facing locators. A crawling inventory is a starting point for manual scenario design, not permission to click destructive or externally side-effecting controls.

Do not promise mathematical zero regressions. Enforce zero *unverified* changes: acceptance criteria are explicit, affected workflows are identified, required tests pass, unresolved failures are visible, and release evidence is recorded. Expand coverage in stages from critical journeys; use stateful/property-based tests for demonstrated sequence-dependent invariants, mutation testing to assess assertion strength, automated accessibility scans plus manual keyboard checks, and telemetry only where privacy and retention are designed. Avoid adding tools that duplicate existing gates without a measurable gap.

Research basis: [Django testing overview](https://docs.djangoproject.com/en/6.1/topics/testing/overview/), [Playwright best practices](https://playwright.dev/docs/best-practices), [Playwright assertions](https://playwright.dev/docs/test-assertions), [HTMX events](https://htmx.org/events/), [HTMX request synchronization](https://htmx.org/attributes/hx-sync/), [Alpine lifecycle](https://alpinejs.dev/essentials/lifecycle), [Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html), [Playwright accessibility testing](https://playwright.dev/docs/accessibility-testing), and [OpenTelemetry Django instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/django/django.html). A relevant 2026 preprint, [Do Context Files Help Coding Agents?](https://arxiv.org/abs/2607.27250), reports no measurable correctness improvement in its bounded study of two agents, 17 tasks, and three repositories; it is not the ETH Zurich/ICLR study described in the supplied text, and its limited sample does not prove instructions are universally ineffective. The practical takeaway is to evaluate agent behavior and executable controls rather than count instruction files.

Browser write flows retain Django's centralized CSRF middleware, include a CSRF token in every rendered POST form, and return a redirect after successful writes. Never replace browser resubmission prompts with JavaScript dialogs; prevent successful POST resubmission with POST-Redirect-GET and render explicit CSRF failures in the app. Unverified accounts must not receive authenticated access or trigger device/workflow side effects.

## Validation ladder

1. Focused regression tests and cross-app contract/integration tests.
2. Full suite using the repository virtual environment's Python interpreter.
3. Django checks and migration consistency using test settings:

   ```powershell
   $env:DJANGO_SETTINGS_MODULE = "app.settings.test"
   .venv\Scripts\python.exe manage.py check
   .venv\Scripts\python.exe manage.py makemigrations --check --dry-run
   ```

4. Dependency consistency with `python -m pip check`.
5. Playwright browser checks are required for frontend/template/layout changes; missing browser setup is a validation failure to resolve, not a reason to silently skip.
6. Browser checks use isolated test settings; production-database/provider validation remains separate and requires explicit authorization and infrastructure.
7. PostgreSQL/Redis/provider verification only when the corresponding isolated services are configured; never use production resources for routine checks.

The test settings use in-memory SQLite and eager Celery execution. They do not certify PostgreSQL-specific behavior, Redis delivery, external providers, or production performance.

## Connected-app boundaries

Treat each app's models and services as owned state. Trace actual callers before changing APIs. Prefer explicit service contracts over implicit signal chains. Make asynchronous handlers idempotent; dispatch side effects after transaction commit when required. Use bounded, observable retries only for demonstrated transient failures. Test rollback, duplicate delivery, retry exhaustion, permissions, and final persisted outcomes when relevant.

Python formatting uses Ruff with the repository's `pyproject.toml` configuration. Firmware-to-blog generation runs synchronously by default; set `FIRMWARE_BLOG_AUTOGENERATION_ASYNC=true` only when a Celery worker is configured and monitored. A configured broker URL by itself does not prove a worker is consuming queued work.

## Frontend, logs, and privacy

Frontend implementation is approved using Django templates, Bootstrap 5.3.9, htmx 4.0.2, Alpine's CSP-safe runtime, Lucide, and Vite; public and staff shells share one htmx runtime. The staff console is a project-owned layer over `django.contrib.admin` (AdminLTE 4.10.2, `@alpinejs/csp`; `apps/core/admin_base.py`, `templates/console/`, `frontend/src/admin/`, `tests/backend/admin/`, `tests/frontend/test_admin_console_*.py`; see `docs/05-frontend/admin-console.md`). Shared tokens and static assets are centralized, with distinct public/admin shells. Do not introduce another framework or duplicate app-local assets. The final visual identity remains subject to product/design review; this does not block the approved functional frontend foundation.

Firmware catalog changes keep provenance/classification, package format, and file extension distinct. Seed commands must be deterministic and additive, guard against non-local databases, and never represent a missing binary as a downloadable firmware record (external records must point at an allowlisted HTTPS host). Firmware-to-blog automation maintains one automated post per brand, model and chipset and lists only staff-approved firmware. When benchmarking public firmware catalogs, document functional patterns and source URLs without copying protected text, branding, or binaries.

Log outcomes and correlation IDs, not credentials, tokens, raw request bodies, or unnecessary personal/device data. Retention must be bounded, scoped, testable, and safe around active writes. Do not purge global environments, arbitrary folders, or logs without explicit scope and authorization.

## Local commands

The project requires Python 3.12 or newer. The configured local settings use PostgreSQL and require a verified `.env` configuration. Tests use the isolated in-memory SQLite settings. See `../README.md` for setup; never run migrations against an unverified database.

Windows:

```powershell
.venv\Scripts\python.exe -m pytest -q
$env:DJANGO_SETTINGS_MODULE = "app.settings.test"
.venv\Scripts\python.exe manage.py check
.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.venv\Scripts\python.exe -m pip check
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\python.exe -m playwright install chromium
```

Run all Python/Django commands through the project's `.venv` (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/macOS). Do not fall back to a globally installed interpreter.

Console frontend changes also need `npm run check` (both Vite builds plus the offline-asset check) and the console Playwright suites (`tests/frontend/test_admin_console_crawl.py` crawls every console URL); see `05-frontend/admin-console.md`.

Before any authorized commit or push, check the VS Code Problems panel. Do not publish while it contains unresolved issues.
