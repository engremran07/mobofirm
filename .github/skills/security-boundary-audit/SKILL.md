---
name: security-boundary-audit
description: Audit a bounded Mobofirm change for security, privacy, authorization, external-service, and existing AI-provider boundary regressions.
argument-hint: "[path]"
---
# Security Boundary Audit

1. Identify changed files, their owning app, actual callers, and applicable
   architecture/governance requirements. Treat repository source and tests as
   evidence; do not infer missing features from configuration examples.
2. Check input validation, authentication/authorization, IDOR boundaries,
   CSRF, escaping, CSP, outbound host allow-lists, credential encryption,
   secret-safe logs, raw IP/User-Agent handling, and bounded resource use.
3. Check provider and AI-related changes against existing policy and product
   approval. Provider-backed AI code is not part of approved product scope;
   retained SEO crawler controls and deterministic firmware-to-blog automation
   are not AI-provider integrations. Do not add a provider or SDK without
   explicit approval.
4. Use maintained, trusted static-analysis tools already available in the
   repository. The `semgrep/mcp` project is archived; do not install or rely on
   it. If a desired scanner is unavailable, state that rather than claiming a
   scan passed.
5. Add focused regression tests for verified issues and run the relevant
   existing checks. Report findings with evidence, severity, affected boundary,
   fixes made, and unverified areas.
