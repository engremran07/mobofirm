---
name: inter-app-automation
description: "Design or repair automation and feature flows that cross blog, comments, firmwares, and tags, including Django signals, tasks, service contracts, retries, and consistency."
---
# Inter-App Automation

Use when a workflow crosses app ownership or runs through a Django signal, task, webhook, or scheduled job.

1. Map the initiating write/request, owning model/service, downstream consumers, transaction boundary, task queue, and user-visible result from real code.
2. Assign one authoritative owner for each piece of state. Prefer explicit service/contract calls over implicit signal chains; preserve existing import conventions.
3. Define behavior for commit/rollback, duplicate delivery, retry, timeout, partial failure, and concurrent updates. Make side effects idempotent; use bounded retries only for identified transient errors, with backoff and observable terminal failure.
4. Keep related database writes atomic. Dispatch external/async side effects after commit; if durable delivery is required, assess an outbox using the project's actual infrastructure rather than inventing a queue.
5. Test each changed boundary: source action, consumer result, permission/validation failure, rollback, duplicate delivery, and retry/exhaustion where applicable. Assert final persisted state and externally observable effects.
6. Check logs/metrics for correlation IDs and useful outcomes without secrets, personal data, or unbounded payloads. Validate migrations and operational recovery steps.
7. Describe consistency guarantees, idempotency key, retry policy, and remaining failure modes.

Do not retrofit a generic event bus, distributed transaction, or new dependency when a small existing service boundary suffices.
