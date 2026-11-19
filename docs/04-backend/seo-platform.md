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
   editable.py / editing.py / fixplan.py / fixscreen.py / analysis.py
                    declared editable fields, atomic ledgered saves, finding-to-field
                    plans, the View-and-fix screen data and in-context page analysis
   providers.py     provider registry (view name → PageSEO)
   policy.py        private paths, robots directives, crawler tokens, canonical query policy
   schema.py        JSON-LD builders (absolute URLs, safe serialisation)
   sitemap_registry.py / sitemap_build.py / sitemap_views.py / sitemap_report.py
   robots.py        RFC 9309 generator, parser and URL tester
   wellknown.py     security.txt, humans.txt, ads.txt / app-ads.txt contract
   httpcache.py     ETag, Last-Modified, conditional GET, HEAD, Cache-Control
   indexnow.py
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
- One sitemap engine (`sitemap_build.py`, fed by the registry in `sitemap_registry.py`).
  `/sitemap.xml` is the index; files are path-based and predictably named
  `/sitemap-<section>[-<partition>]-<n>.xml` (and `.xml.gz`, a plain gzip body with
  `Content-Type: application/gzip`, no `Content-Encoding`). Sections: static, pages,
  blog, brands, chipsets, models, firmware, tools, tags; models and firmware are split
  per brand slug and chipsets per chipset-vendor slug so every file stays small and has
  its own `lastmod`. The old `/sitemap-<section>.xml[?p=N]` answers a permanent
  redirect to `/sitemap-<section>-<N>.xml` (partitioned sections redirect to the
  index). Page size is `SEOSettings.sitemap_page_size` (default 5000), capped at the
  protocol's 50,000 URLs; a rendered file over 50 MB is refused with a logged 500
  instead of being truncated.
- Membership: apps register *filters* so a URL is listed only if the resolver would
  index it and it is its own canonical. `lastmod` is a W3C Datetime (UTC, seconds) of
  the real update time, or omitted (never "now", future times dropped); the static
  entries and anything without a stored timestamp carry none. No priority/changefreq;
  scheme and host come from `SITE_URL`. The index lists each file with the newest real
  `lastmod` of its entries.
- Image extension: `<image:image>` is added only for an image that exists in media or
  static storage, is served from this site, has an image suffix, is not under a
  private path and is allowed for `Googlebot-Image` by the generated robots.txt
  (`SEOSettings.sitemap_images_enabled`). A section class may define `images(item)`;
  otherwise the conventional attributes `cover_image_url`, `image_url`, `logo_url`,
  `hero_image`, `og_image` of the item are considered.
- News sitemap (`/sitemap-news.xml`): off by default (`sitemap_news_enabled`); only
  posts from sections that define `news(item)` (the blog), already filtered as
  indexable and canonical, published within the last 48 hours, newest first, at most
  1000. It is added to the index and as a second `Sitemap:` line in robots.txt.
- Video sitemap: not generated. No video metadata (title, description, thumbnail,
  content or player URL) exists in the data model, and inventing it is prohibited.
- hreflang: `<xhtml:link>` alternates appear only for a section that declares
  `alternates(item)` with at least two real localized URLs; none does today.
- HTTP behaviour (`httpcache.py`): GET and HEAD only, `ETag`, `Last-Modified` (the
  newest real `lastmod`), conditional GET (304), `Cache-Control: public, max-age=300,
  stale-while-revalidate=3600`, `X-Robots-Tag: noindex` on sitemap responses. XML is
  styled by a same-origin stylesheet (`static/seo/sitemap.css`, linked with an
  `xml-stylesheet` PI) that lays the file out as a table: no inline style or script, so
  the strict CSP stays intact. XSLT is deliberately not used: browsers have deprecated
  it and Chromium injects a warning banner (inline style, CSP errors) into any
  XSLT-transformed page.
- `robots.txt` (`robots.py`, RFC 9309): one group for `*`, `Googlebot`,
  `Googlebot-Image` and `bingbot` with the private Disallow list, duplicate-URL traps
  with wildcards (`/*?sort=`, `/*&sort=`, `order`, session ids, `utm_*`, `gclid`,
  `fbclid`, `msclkid`) and `Allow` for `/static/` and `/media/`, ordered longest
  pattern first. AI crawler groups come from `policy.py` only and follow the three
  settings switches: search (`OAI-SearchBot`, `PerplexityBot`, `Claude-SearchBot`),
  user-triggered (`ChatGPT-User`, `Claude-User`, `Perplexity-User`,
  `Meta-ExternalFetcher`) and training (`GPTBot`, `ClaudeBot`, `anthropic-ai`,
  `Google-Extended`, `Applebot-Extended`, `CCBot`, `Bytespider`, `Meta-ExternalAgent`,
  `Amazonbot`, blocked by default). The tokens follow each vendor's published crawler
  documentation as last known; re-verify them when a vendor announces a new crawler.
  `Crawl-delay` is written only for `bingbot` (`robots_crawl_delay`, 0 = none; Google
  ignores it). `?page=N` is not blocked: robots patterns cannot express a numeric
  limit, so pagination stays with the resolver (noindex, canonical). The file carries
  a generation comment, stays far below 500 KiB, is `text/plain; charset=utf-8`, and
  has an ETag, 304 and HEAD support. One parser (`robots.parse`) implements RFC 9309
  matching (group selection, longest match, Allow wins ties, `*` and `$`,
  percent-encoding); the crawler, the staff URL tester and the sitemap image check all
  use it.
- Well-known files (`wellknown.py`): `/.well-known/security.txt` (RFC 9116 Contact,
  Expires, Policy, Canonical from `SEOSettings`; served only while configured and not
  expired), `/humans.txt` (off by default), `/ads.txt` and `/app-ads.txt` (404 unless an
  app contributes lines through `register_ads_lines`; `apps/ads/seo_providers.py`
  contributes the AdSense line only when ads and AdSense are enabled with a valid
  publisher ID; no app contributes `app-ads.txt`). There is deliberately no `llms.txt`:
  no search engine documents it as a signal and Google states it needs no special file.
- IndexNow: key file route `/<key>.txt` (only when enabled and matching), submission
  through SafeFetcher in Safe autopilot/Full from the job outbox (committed content
  change, one job per object and state per hour, duplicates collapse on the job key,
  retry with backoff, terminal failure after `max_attempts`). It notifies
  participating engines, not Google, and never guarantees indexing. Google retired the
  sitemap ping, so nothing pings; submit the sitemap once in Search Console and Bing
  Webmaster Tools (the staff page says so).
- Feeds (owned by `apps.distribution`, see `distribution.md`): the public RSS/Atom/JSON
  feeds under `/feeds/` use the existing seams only. `feeds:index` has a page provider
  (`apps/distribution/seo_providers.py`); it is not added to the sitemap registry (the
  registry test pins the section set, so listing the index is left to the SEO owner via
  `register_sitemap`); the Google News feed is available only while
  `sitemap_news_enabled` is on and lists exactly the posts `sitemap_build.news_entries`
  returns (so it never contradicts the news sitemap); WebSub pings go to the configured
  hub, and an IndexNow ping for a newly public URL is queued through
  `apps.seo.indexnow.submit` only while IndexNow is enabled here (no second client). The
  public `<head>` advertises the site-wide feeds with `<link rel="alternate">`
  (`{% feed_autodiscovery %}` in `templates/base.html`).
- `Redirect` validation rejects unsafe targets (scheme-relative, backslash, control
  characters), loops and over-long chains on every save; the middleware serves only
  GET/HEAD, only safe stored targets, and only when the view returned 404.

## Content scan

`apps/seo/scan.py` (`manage.py seo_scan`, the **Run scan now** buttons, Celery
`apps.seo.tasks.scheduled_scan` every 6 hours) audits every public URL with no HTTP, no
`SITE_URL` reachability and no Celery: parameterless routes with a registered provider
plus every item of every sitemap section (pages, posts, brands, chipsets, models,
firmware, tools, tags) are resolved in-process by the same resolver that builds the
real page head. Each URL is checked against the standards in `apps/seo/rules.py`:
title length (25-60) and generic or missing titles, description length (70-160),
generic, truncated and duplicate titles and descriptions, canonical not self, noindex
on public content (thin pages are `info`), social image, JSON-LD presence and required
properties, slug quality, sitemap URLs without a route, and the object-aware checks in
`object_checks.py`: thin posts and pages, images without `alt`, `<h1>` in a post body,
missing summary, duplicate opening text, model without chipset, tool linked to no
chipset or brand, brand without description, and flash-guide coverage per model and
chipset. A scan is bounded by a time budget (25 s from the screen, 10 minutes in the
background); when it stops early the run is `partial` and the next run resumes at its
cursor. Issues are upserted by fingerprint, reopened when they reproduce, ignored ones
stay ignored, and fixed or removed pages are resolved automatically. `PageSnapshot`
stores the last measured head per URL (the inventory). Coverage: parameterless routes
with a provider (including the `/feeds/` index, whose provider `apps/distribution`
registers; it is also listed in the static sitemap section) and every sitemap item
(posts and flash guides, firmware files, brands, models, chipsets, tools, tags, pages);
pages without a content object are owned by their route so each finding is fixable.

## Render crawl and audit

`run_crawl()` crawls from `/` and the sitemap: robots-aware, same-origin, bounded by
`crawl_max_pages`, raw HTML only (no browser rendering). When the origin is one of this
deployment's `ALLOWED_HOSTS` (or `SEO_CRAWL_TRANSPORT = "inprocess"`) pages are served
in-process through Django's request handler (`apps/seo/inprocess.py`), so it works from
a dev server and needs no network; any other host goes through SafeFetcher
(`SEO_CRAWL_TRANSPORT = "http"` forces that). An in-process request carries a private
identity (`apps/core/internal_request.py`): a WSGI environ entry that remote clients
cannot send (servers copy headers only into `HTTP_*` keys) and that must equal a
per-process secret, so a request header is never trusted. Rendering and authorization
run as for an anonymous visitor, while `should_count`/`is_bot_request`, ad impressions,
referral clicks, marketplace views and short-link clicks stay silent, so a crawl leaves
every counter and analytics table unchanged. The in-process transport never requests
private, download or token URLs and refuses every method but GET and HEAD. It records
measured inbound/outbound link counts on
the inventory. It stores `CrawlRun` / `PageObservation` and upserts `Issue` rows by
fingerprint (closed when no longer reproduced). Checks: fetch failures, sitemap URL
errors/redirects, redirect chains and links to redirects, broken internal links,
missing/duplicate/multiple titles and descriptions, missing/multiple/relative/offsite
canonicals, canonical-to-error and sitemap/canonical conflicts, noindex or
robots-blocked URLs in the sitemap, multiple robots tags, missing/multiple `<h1>`,
invalid JSON-LD, missing image `alt`, slow responses, orphan pages, hreflang
reciprocity/validity/self-reference, and `head_mismatch` (served head versus resolver
intent). The only health figure is the share of crawled pages without an error-level
issue; there is no SERP or ranking score.

## Review and fix workflow

The issues board (`/seo/issues/`) groups open issues by object type (Posts, Firmware,
Models, Brands, Chipsets, Tools & guides, Pages & tags, Site-wide) with search,
severity and check filters, HTMX updates and pagination. There is **no fixable /
non-fixable split**: every open issue on every object type has a fix path, and the
board shows no such filter or badge (`Issue.fixable` stays a stored internal hint; the
"has a proposal" state is derived from `Issue.suggestion`).

**View and fix** (`/seo/fix/?issue=<id>` or `?url=<path>`; `issues/<id>/` redirects
there) is one full screen per page that lists *all* open issues of that page or object
and edits the offending fields inline:

- Owner: the content object behind the URL, or, for a page without one (home,
  listings, hubs, the feeds index), the route itself (`seo.seosettings` /
  `route:<path>`). The resolver reads overrides for that reference, so every scanned
  URL is fixable and no issue is ever "without an object".
- Context: a live search-result preview (HTMX, server-rendered), the heading outline,
  every image with its alt state, canonical, robots, Open Graph, Twitter, JSON-LD types
  and internal-link counts, read from the page rendered in-process
  (`analysis.py`; the in-process crawler identity keeps counters untouched).
- Editable fields: all SEO override fields (`fixes.OVERRIDE_SPECS`: meta title and
  description, canonical, robots directives, noindex, nofollow, social title,
  description and image) for any page; declared object fields (`editable.py`: slug,
  summary, description) through the model's own `save()`; alt text for body images that
  lack it; and body `<h1>` to `<h2>`. A slug is editable only where the SEO content
  observers watch the model, so the old URL redirect proposal and republish run through
  the existing job outbox. Findings highlight their fields (`fixplan.py`); every other
  field sits in a collapsed "All SEO fields" block. Findings that live in the content
  itself (thin text, schema author/date, missing chipset link) open the owner's console
  edit form at the field (`#id_<field>`); flash-guide findings link to the guides screen.
- Each save (`editing.apply_changes`): validate everything first, write in one
  transaction (each change a `ChangeLedger` entry with before/after: `metadata_fix_*`
  for overrides, `object_fix` for object fields; the ledger accepts a model only when
  `editable` declares it), skip values equal to the stored one (a repeated submission
  writes nothing), re-audit the page, and on commit *republish*: a
  `content_changed` job (sitemaps are generated live; IndexNow follows the autonomy
  mode). A rejected form keeps what was typed in the session and redirects back.
- Buttons: **Save and re-audit**, **Save and next issue**, **Re-audit only**; every
  POST ends in a redirect (`HX-Redirect` for HTMX), so a reload never resubmits.
- Permission model: `seo.change_metadata` saves (override and the declared object
  fields, all ledgered and roll-back-able by an administrator); `seo.change_issue`
  ignores a finding with a reason. The **Moderators** and **Editors** groups get
  `view_issue`, `change_issue`, `change_metadata` and `view_changeledger` from
  `manage.py bootstrap_site` (`GROUP_PERMISSIONS`, safe to re-run); rollback, approvals,
  scans and settings stay administrator-only. Staff without `change_metadata` can read
  the screen but cannot save.

- **Apply all safe suggestions** previews and applies only the "add" fixes (missing
  titles and descriptions) in the current filter; rewrites are never part of the bulk.
- After each scan the autonomy mode decides: `suggest` applies nothing,
  `safe_autopilot` applies the "add" fixes, `full` also the rewrites, `off` disables
  scans and fixes. Only title, description and noindex proposals are ever applied
  unattended. Staff pressing Save is the approval and works in every mode but `off`.
- Google Discover readiness (blog posts): lead image present and at least 1200 px wide
  (measured only for site-hosted files), `max-image-preview:large` allowed, Article /
  BlogPosting / NewsArticle JSON-LD with `datePublished`, `dateModified` and an author.

## Interlinking

`apps/seo/interlinking.py` proposes links only from real relations in the entity graph
(brand has model, model uses chipset, tool supports chipset or brand, post covers brand
or model) plus model to the tools of its chipset and chipset to its flashing-guide
post. Each proposal stores the relation, a reason and an anchor rotated per target; it
is capped by a per-source link budget, never links a source to itself or twice to the
same target, and honours `InterlinkExclusion`. `/seo/links/` lists proposals to accept,
reject or reopen (single and bulk); rejected pairs are never proposed again. Accepted
links reach templates through `{% load seo_tags %}{% related_links object %}` and
`apps.seo.api.related_links`; `apps.seo.api.link_body_html` inserts accepted links into
a post body and adds `rel="nofollow noopener noreferrer"` to external links. Using the
tag in the public model, brand, chipset and tool templates and `link_body_html` in the
blog renderer belongs to those apps.

## Flash guides

The firmware automation owns one post per model and per chipset with a "How to flash
firmware on ..." section, recommended tools and the firmware list. `/seo/guides/`
audits that coverage (guide present, supporting tool, chipset link, firmware list) and
refreshes the posts through the registered task
`apps.firmwares.tasks.sync_automated_blogs`, run synchronously so no broker is needed.
The issue codes `model_missing_flash_guide`, `chipset_missing_flash_guide` and
`chipset_no_tool` feed the board.

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

`/seo/dashboard/`, `scan/`, `issues/` (with `issues/<id>/` redirecting to `fix/`,
`issues/bulk/`), `fix/` (View and fix, `fix/apply/`, `fix/preview/`),
`links/`, `guides/`, `inventory/`, `sitemaps/` (sitemap sections with URL count, files,
latest lastmod, size estimate and validation, the robots.txt preview, a URL tester that
uses the same parser, the difference from the file before the latest settings change and
the ledgered history of robots-relevant settings), `inspect/`, `ledger/`, `jobs/`,
`settings/`. Every
screen renders in the staff console shell (`render_admin_page`, `admin/staff_page.html`
and the shared console components). Read views need `is_staff` plus the model view permission; actions
need `seo.run_crawl` (scan, crawl), `seo.change_metadata` (apply fixes),
`seo.change_issue` (ignore, restore, re-audit, generate guides),
`seo.change_linksuggestion`, `seo.approve_changeledger`, `seo.rollback_changeledger`,
`seo.change_seojob` (**Process queue now** drains the outbox without Celery) or
`seo.change_seosettings`. All POST forms carry CSRF tokens and redirect after success.

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

Browser-rendered crawling and Core Web Vitals field storage, a video sitemap (no video
metadata exists), localized `hreflang` URLs (no localized URLs exist), `llms.txt`
(deliberately omitted), `app-ads.txt` lines (no app contributes any), HTMX
`head-support` (extension not installed; boosted navigation updates `<title>` only), AI
content recommendations, firmware comparison pages and provenance/verification fields
(belong to `firmwares`), and external-link checking.
