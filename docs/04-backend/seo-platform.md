# SEO platform (`apps.seo`)

`apps.seo` is the single Autonomous SEO Control Center: one settings authority, one
metadata resolver, one sitemap registry, one outbound HTTP path, a real crawler and an
audited, reversible automation engine. It is a bounded context inside the Django
project, not a separately deployed service. This document is the repo-specific
refinement of the owner's SEO brief and doubles as the implementation prompt for
agents extending the app. Running code, settings and migrations are the implemented
state; if this file and the code disagree, report it.

## Architecture

```text
domain apps (firmwares, blog, tags, pages)
   └─ seo_providers.py  ──register──►  apps.seo
        page providers · content sources · sitemap filters · graph sources
apps.seo
   resolver.py      one ResolvedSEO per response (precedence + provenance)
   providers.py     provider registry (view name → PageSEO)
   policy.py        private paths, robots directives, canonical query policy
   schema.py        JSON-LD builders (absolute URLs, safe serialisation)
   sitemap_registry.py / sitemap_views.py / robots.py / indexnow.py
   redirects.py     validation, loop detection, chain flattening
   fetcher.py       SafeFetcher — the only outbound HTTP path
   crawler.py / audit.py / inventory.py   crawl, measured checks, page inventory
   ledger.py / autonomy.py / jobs.py      change ledger, policy gates, outbox jobs
   integrations/    PageSpeed, Search Console, status page (all disabled by default)
   entity_graph.py  entity graph from real relations
   views.py, admin.py, templates/seo/*, frontend/src/styles/seo*.css
```

`apps.seo` never imports another domain app's models at module level. Domain apps
contribute through `<app>/seo_providers.py`, auto-imported in `SeoConfig.ready()`
(`autodiscover_modules("seo_providers")`). An app without that file simply
contributes nothing; every unmapped route defaults to `noindex`.

## Metadata contract

`{% seo_head %}` (in `templates/base.html`, rendered once) calls
`resolver.resolve(request, context)` and renders `templates/seo/head.html`: one
`<title>`, description, robots, canonical, hreflang (only when a provider declares
real alternates), Open Graph, Twitter and JSON-LD data blocks. No inline scripts or
styles; JSON-LD is escaped so it cannot close its element. Child templates must not
emit any managed head tag (`tests/backend/seo/test_integrity.py` scans for this).

Precedence (highest first): non-overridable indexation policy → explicit `Metadata`
override on the content object → verified object data from the provider → provider
page-type defaults → `SEOSettings` defaults → conservative fallback. Each field's origin
is in `ResolvedSEO.provenance` and shown in the page inspector.

Rules the resolver enforces:

- Canonical URLs are absolute from `SITE_URL` (request origin only as a visible
  fallback; the audit raises `site_url_missing`). Tracking parameters never enter the
  canonical; `?page=N` (N>1) and provider-declared parameters do; any other parameter
  makes the response `noindex, follow` with the base canonical.
- Private areas, unmapped routes, thin pages and unknown routes are `noindex`.
  Policy cannot be overridden into `index` by an administrator override.
- No invented facts: descriptions come from stored fields or counts of real rows;
  dates come only from stored timestamps; no checksums or specifications are
  fabricated. `SEOSettings.auto_meta_enabled` controls derived text only.
- `SoftwareApplication` is emitted only for flashing tools; firmware files get
  `WebPage`; listings get `CollectionPage` + `ItemList`; FAQ markup is not emitted
  (Google removed FAQ rich results).
- `X-Robots-Tag: noindex, nofollow` is sent for private paths, download pages and JSON
  responses (`RobotsHeaderMiddleware`).

### Page types and indexation

| Page type | Index when |
| --- | --- |
| home (`/`); `/firmwares/` canonicalises to `/` | always |
| brand / chipset / model | at least the configured minimum of active models / public firmware files (`SEOSettings.min_*`) |
| firmware detail | active and approved (same queryset as the public catalogue); type aliases share one canonical |
| firmware category (`browse?type=`) | known category with public files; other filters → noindex |
| tools hub / tool detail, guides hub | tools exist / always |
| blog list, category, archive, brand/model posts | published posts exist; filters → noindex |
| blog post | live and not `noindex`; honours its own `canonical_url` |
| tag | active and at least `min_posts_for_tag_index` live posts |
| pages index / page | published and public |
| search, account, admin, download/token | never |

There is no standalone variant page route, so there is no variant provider; variants
are described on the model page.

## SafeFetcher

All outbound HTTP in `apps.seo` goes through `SafeFetcher` (a code test fails on any
other HTTP client). It allows only `http`/`https` without credentials on allowed
ports, resolves each hostname once, requires every resolved address to be public
(loopback, private, link-local/metadata, CGNAT, multicast, reserved, IPv4-mapped,
6to4, Teredo and NAT64 are refused), pins the socket to the validated address
(defeats DNS rebinding), follows redirects manually and revalidates every hop, caps
redirects, bytes, total time and concurrency (global and per host), never sends
cookies, and sends `Authorization` only to an explicitly allow-listed first host.
`SEO_FETCH_ALLOW_PRIVATE_NETWORKS` is `False` in base settings and `True` only in
`app/settings/local.py`.

## Discovery, sitemaps, robots, IndexNow, redirects

- `inventory.build_inventory()` walks the real URL conf and every sitemap section,
  classifies each URL through the resolver and flags conflicts (`noindex_in_sitemap`,
  `canonical_mismatch`, `private_in_sitemap`).
- One sitemap registry (`/sitemap.xml` index, `/sitemap-<section>.xml`). Sections:
  static, pages, blog, brands, chipsets, models, firmware, tools, tags. Apps register
  *filters* so a URL is listed only if the resolver would index it and it is its own
  canonical. `lastmod` is the real update time or omitted; no priority/changefreq; the
  scheme and host come from `SITE_URL`. Image and news sitemaps are not enabled (no
  verified image or news metadata exists). XML is styled by a self-hosted CSS file
  (`build/seo-sitemap.css`) so the strict CSP stays intact.
- `robots.txt` is generated from settings: private Disallow list, a Sitemap line, and
  AI crawler groups (`OAI-SearchBot`/`PerplexityBot`/`Claude-SearchBot` search bots,
  `ChatGPT-User`/`Claude-User`/`Perplexity-User` user agents, training bots) toggled
  independently; training bots are blocked by default.
- IndexNow: key file route `/<key>.txt` (only when enabled and matching), submission
  through SafeFetcher in Safe autopilot/Full. It notifies participating engines, not
  Google, and never guarantees indexing.
- `Redirect` validation rejects unsafe targets (scheme-relative, backslash, control
  characters), loops and over-long chains on every save; the middleware serves only
  GET/HEAD, only safe stored targets, and only when the view returned 404.

## Crawler and audit

`run_crawl()` crawls from `/` and the sitemap through SafeFetcher: robots-aware,
same-origin, bounded by `crawl_max_pages` and delay, raw HTTP HTML only (no browser
rendering). It stores `CrawlRun` / `PageObservation` and upserts `Issue` rows by
fingerprint (closed when no longer reproduced). Checks: fetch failures, sitemap URL
errors/redirects, redirect chains and links to redirects, broken internal links,
missing/duplicate/multiple titles and descriptions, missing/multiple/relative/offsite
canonicals, canonical-to-error and sitemap/canonical conflicts, noindex or
robots-blocked URLs in the sitemap, multiple robots tags, missing/multiple `<h1>`,
invalid JSON-LD, missing image `alt`, slow responses, orphan pages, hreflang
reciprocity/validity/self-reference, and `head_mismatch` (served head versus resolver
intent). The only health figure is the share of crawled pages without an
error-level issue; there is no SERP or ranking score.

## Autonomy engine

| Mode | Behaviour |
| --- | --- |
| Off | nothing is recorded or applied; crawls refuse to run |
| Suggest (default) | every change is a proposal in the approval queue |
| Safe autopilot | low-risk reversible changes apply and are verified; the rest wait |
| Full | low and high risk apply; protected policies still wait |

Risk: `redirect_create`, `redirect_flatten` low; `redirect_retarget`, `redirect_delete`
high; everything else (robots/AI policy, settings, canonical policy, unknown kinds)
protected and approval-only in every mode.

- Events: registered models call `transaction.on_commit` → `SEOJob` rows (outbox),
  debounced to one job per object and state per hour, collapsing duplicate delivery.
  Firmware sources fire only on URL or publication changes; blog posts and pages also
  on edits.
- Jobs: Celery beat runs `apps.seo.tasks.process_pending_jobs` every minute and
  `scheduled_crawl` daily; `manage.py seo_process_jobs` does the same by hand. Retry
  with backoff, terminal failure after `max_attempts`, stale `RUNNING` jobs are
  reclaimed. `SUCCEEDED` only after every attempted action was verified; nothing to do
  is `SKIPPED`; failures keep `last_error`.
- Ledger: `ChangeLedger` stores before/after, reason, source, mode, validation,
  decider and timestamps. Apply writes then verifies stored state inside one
  transaction (failure rolls back). Rollback is conflict-checked (refused if the target
  changed since). Admin and settings edits are ledgered as `manual_edit`.
- A moved public URL (slug change) records a permanent redirect through this path;
  `redirect_chain` issues become `redirect_flatten` changes.
- Entity graph (`seo_rebuild_graph`) is built only from foreign keys
  (brand → model → chipset, tool → brand/chipset, post → brand/model). Link suggestions
  are never auto-inserted into content.

## Integrations (disabled by default)

IndexNow, Google Search Console (`SEO_GSC_ACCESS_TOKEN`, `SEO_GSC_PROPERTY`) and
PageSpeed Insights (`SEO_PAGESPEED_API_KEY`) report `disabled`, `not_configured` or
`ready`, never placeholder data. Bing Webmaster is `unsupported` (legacy APIs retired;
Bing is notified by IndexNow). Secrets come from the environment, not the database.

## Control Center (staff)

`/seo/dashboard/`, `inventory/`, `inspect/`, `issues/`, `ledger/`, `jobs/`,
`settings/`. Read views need `is_staff`; actions need `seo.run_crawl`,
`seo.approve_changeledger`, `seo.rollback_changeledger`, `seo.change_seojob` or
`seo.change_seosettings`. Views are thin; templates extend `admin/base_site.html` and
load `build/seo.css`, so the planned admin migration can restyle them without logic
changes. All POST forms carry CSRF tokens and redirect after success.

## Phases and acceptance gates

1. Resolver and head contract.
2. SafeFetcher.
3. Discovery, sitemaps, robots, IndexNow, redirects.
4. Crawler and audit.
5. Internal links and entity graph.
6. Autonomy engine.
7. Control Center.
8. Tests.

A phase is done only when its tests in `tests/backend/seo/` (and
`tests/frontend/test_seo_browser.py` for the head and dashboard) pass in the full
suite, `manage.py check` and `makemigrations seo --check` are clean, and production
head output is checked with the crawler.

## Prohibitions

No second settings model, metadata generator or sitemap engine; no outbound HTTP
outside SafeFetcher; no AI during page rendering; no AI-generated canonical,
indexation, robots or redirect change outside the policy engine; no thin or
auto-created pages to chase rankings; no invented dates, firmware facts or checksums;
no SERP/ranking scores; no job marked successful before its effect is persisted and
verified; no mass robots/canonical/redirect change without approval.

## Not built (explicit)

Browser-rendered crawling and Core Web Vitals field storage, image/news sitemaps,
`hreflang` output (no localized URLs exist), HTMX `head-support` (extension not
installed; boosted navigation updates `<title>` only), AI content recommendations,
firmware comparison pages and provenance/verification fields (belong to `firmwares`),
and external-link checking.
