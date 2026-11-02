---
name: observability-retention
description: "Design or debug Django/Python structured logs, telemetry, live diagnostics, sensitive-data controls, time-based retention, and safe log cleanup."
---
# Observability and Retention

Use for application logs, telemetry, incident diagnosis, log rotation, collection consent, or retention/cleanup workflows.

1. Trace existing logging and telemetry configuration, sinks, operators, privacy/consent behavior, storage limits, and deployment ownership. Do not assume a mobile APK or telemetry backend exists.
2. Emit structured, actionable events with severity, timestamp, component, outcome, and a request/task correlation identifier where available. Record enough to diagnose the failure; never log secrets, auth tokens, full request bodies, or unnecessary personal/device identifiers.
3. Keep logging failure from corrupting an unrelated business operation only when that is the explicit contract; surface dropped telemetry through a safe counter/health signal rather than silently pretending collection succeeded.
4. Set an explicit configurable age/size retention policy and bounded cleanup cadence. Prefer supported sink rotation/lifecycle rules. Cleanup must be scoped to the designated log store, safe under concurrent writes, auditable, and dry-run/testable; never delete arbitrary paths or run broad recursive purges.
5. Test event shape and redaction, retention boundary (including timezone/clock edge cases), size/cap behavior, concurrent/active files, permissions, and cleanup error reporting. Verify important events remain diagnosable after purge.
6. For mobile diagnostics, collect only when a real client exists and the product's consent, permissions, transport security, storage limits, and retention policy are defined. Redact on-device and server-side.
7. Report data captured, storage/retention guarantees, failure mode, and operator verification steps. Use `deep-verification` to investigate issues that ordinary tests cannot reproduce.
