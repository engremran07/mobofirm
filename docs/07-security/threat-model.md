# Threat Model (Initial)

This is an initial trust-boundary inventory, not a security certification. Revisit it when product journeys, deployment, and final visual design are approved.

## Assets

- Account credentials, MFA state, profile and notification data.
- Device identity and policy records.
- Firmware binaries, metadata, download tokens, and signed-file URLs.
- Ad/affiliate credentials, consent state, and event records.
- User-generated posts, comments, tags, and governance audit history.
- Database credentials, Django secret key, and external provider credentials.

## Trust boundaries and threats

| Boundary | Representative threats | Required controls/evidence |
|---|---|---|
| Anonymous client → Django | CSRF, account enumeration, injection, abusive traffic, forged consent/device payload | Server-side validation, Django protections, scoped rate limits, permission tests |
| Authenticated user → owned resources | IDOR, privilege escalation, cross-account profile/device access | Object-level ownership, least privilege, cross-owner regression tests |
| Upload/download → local firmware storage | Path traversal, unauthorized download, token replay, hotlinking, file replacement | Canonical path/authorization checks, expiring tokens/signatures, file-type/size policy, audit tests |
| Content submitter → governance | Self-approval, duplicate actions, invalid state transitions, audit tampering | Separate permissions, locked transitions, idempotency keys, append-only events |
| Application → provider/broker/database | Credential leakage, SSRF, compromised integration, retry storms | Secret management, allowlisted endpoints, bounded retries, timeouts, redacted logs |
| Browser ↔ Django and external resources | CSP bypass, unsafe inline execution/styles, overbroad CORS, mixed content, ad tracking without consent | Strict CSP, same-origin default, HTTPS-only resources, no inline scripts, one fixed hash for DRF syntax CSS, consent-gated ad rendering, browser/header regression tests |
| Operator → retention/admin workflows | Excessive data deletion or privilege misuse | Explicit scope, dry-run, positive retention window, reviewed access, test coverage |

## Unknowns requiring review

- Production hosting/network topology and proxy trust.
- Firmware malware/provenance scanning and legal ownership controls.
- Data classification, user consent/legal basis, retention and deletion obligations.
- External identity, ad, affiliate, email, and storage provider threat surfaces.
- Rate limits, incident response, backup/restore, and key rotation in deployment.
