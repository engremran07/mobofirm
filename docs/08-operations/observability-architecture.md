# Observability Architecture

## Current evidence

- Django logging is configured to emit standard formatted logs to the console, with `DJANGO_LOG_LEVEL` control.
- `apps.core.utils.logging` provides a structured `event` helper, but the settings do not configure a centralized log/metrics backend.
- `/health/live/` reports process-level liveness. `/health/ready/` attempts a database connection and returns 503 when it fails.
- The bounded `cleanup_old_data` command handles expired sessions, completed firmware download sessions, and old ad events; it supports dry run and rejects non-positive ages.

## Operational requirements

- Include correlation/request identifiers where available; log outcomes and useful identifiers, not credentials, tokens, raw payloads, or unnecessary personal/device data.
- Keep health checks free of sensitive details. Readiness must reflect dependencies required to serve requests; do not use liveness as a database health check.
- Alert on sustained readiness failure, elevated 5xx/authorization anomalies, queue failures, and firmware download errors after an approved monitoring backend is selected.
- Define retention, access, export, and deletion for logs, events, and backups with the data owner before production launch.
- Use the cleanup command only against a verified target and with reviewed age/scope. Dry-run before any approved deletion.

## Not configured

No hosted logging, tracing, metrics, alerting, queue dashboard, or production retention service is selected in the current repository. Add none without an approved operations design.
