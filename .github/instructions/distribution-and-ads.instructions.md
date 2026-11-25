---
description: "Use when changing ads, social distribution, adapters, click tracking, feeds, or generated media."
applyTo: ["apps/ads/**/*.py", "apps/distribution/**/*.py"]
---
# Ads and Distribution

- Preserve the owning app boundaries and existing adapter/registry contracts.
  All outbound platform requests go through `apps.seo.fetcher.SafeFetcher` with
  explicit allowed hosts; scrub credentials from persisted errors and logs.
- Verify provider API versions and request formats against current official
  documentation before changes. Do not assume the version in a task example is
  current or change `GRAPH_VERSION` without compatibility tests.
- Do not record raw IP addresses or User-Agent strings in analytics. Keep click
  redirects same-origin, GET/HEAD-only, and protected against open redirects.
- Keep channel credentials encrypted and channels disabled until staff enable them.
  Respect opt-in, platform limits, consent requirements, approval modes, retries,
  idempotency, and the existing circuit breaker.
- Preserve streaming for large media and bounded Celery work. Do not perform
  synchronous high-volume event writes during page rendering or redirects.
- Test adapters with stub fetchers and cover secret scrubbing, allow-list
  rejection, idempotent retries, rate limits, and terminal failures.
