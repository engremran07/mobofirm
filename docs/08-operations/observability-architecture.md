# Observability Architecture

## Current evidence

- Django logging writes standard formatted logs to the console (`DJANGO_LOG_LEVEL`); setting `LOG_DIR` adds a daily-rotated file kept for `LOG_RETENTION_DAYS` days.
- `apps.core.utils.logging` provides a structured `event` helper, but the settings do not configure a centralized log/metrics backend.
- `/health/live/` reports process-level liveness. `/health/ready/` checks the database and the shared cache, returns 503 when either fails, and reports only `ok`/`unavailable` (failure details are logged, not returned).
- The bounded `cleanup_old_data` command handles expired sessions, expired API tokens, completed firmware download sessions, and old ad events; it supports dry run and rejects non-positive ages. `purge_old_device_events` enforces `DEVICE_EVENT_RETENTION_DAYS` for device events, and both run from the Celery beat schedule. See `deployment.md` for the production topology.

## Operational requirements

- Include correlation/request identifiers where available; log outcomes and useful identifiers, not credentials, tokens, raw payloads, or unnecessary personal/device data.
- Keep health checks free of sensitive details. Readiness must reflect dependencies required to serve requests; do not use liveness as a database health check.
- Alert on sustained readiness failure, elevated 5xx/authorization anomalies, queue failures, and firmware download errors after an approved monitoring backend is selected.
- Define retention, access, export, and deletion for logs, events, and backups with the data owner before production launch.
- Use the cleanup command only against a verified target and with reviewed age/scope. Dry-run before any approved deletion.

## Not configured

No hosted logging, tracing, metrics, alerting, queue dashboard, or production retention service is selected in the current repository. Add none without an approved operations design.
