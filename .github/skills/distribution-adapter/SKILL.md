---
name: distribution-adapter
description: Build or modify one of Mobofirm's social, messaging, or webhook adapters in apps/distribution while preserving platform and privacy contracts.
argument-hint: "[platform_kind]"
---
# Distribution Adapter

1. Read `docs/04-backend/distribution.md`,
   `apps/distribution/channels.py`, `platforms.py`, `catalog.py`, and the
   adapter tests. Confirm that the platform is implemented and registered before
   changing it.
2. Verify endpoint, API version, scopes, media requirements, and limits against
   current official provider documentation. Do not copy API versions or feature
   claims from prompts.
3. Route every outbound request through the existing `SafeFetcher` adapter
   contract with explicit host allow-lists. Preserve credential encryption,
   secret scrubbing, opt-in and consent rules, and the no-raw-IP/UA analytics
   contract.
4. Preserve idempotency across multi-step publication and retries. Retry only
   known transient errors with bounded backoff; terminal refusals must not loop
   or be reported as sent.
5. Update the adapter registry and presentation catalogue together, retaining
   truthful setup and API documentation. Do not claim an assisted/manual
   platform posted automatically.
6. Test with stub fetchers, including success, rejected host, secret redaction,
   rate limiting, retry/duplicate delivery, and terminal failure.
