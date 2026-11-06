# Definition of Done

This definition applies to backend changes and governance deliverables. Product and visual-design approval remain separate gates.

## Required

- The requirement and approval state are recorded; code behavior is not treated as proof of product approval.
- The owning app, caller path, permissions, side effects, and integration contracts are understood.
- A user-visible behavior change has a focused regression test. A cross-app change has a contract/integration test.
- Success and meaningful rejection/failure cases are asserted. Duplicate delivery and transaction behavior are covered where relevant.
- No unavailable-app imports or accidental legacy integrations are introduced.
- Any migration is reviewed for data loss, reversibility, and operational impact. It is not applied to a non-test database without explicit approval.
- Focused tests pass, followed by the full suite and the applicable Django, migration, and dependency checks.
- The VS Code Problems panel has been checked; no unresolved diagnostics remain for a change to be committed or pushed.
- Every project test lives under root `tests/backend/` or `tests/frontend/`; CI collects the complete root test suite and reports zero unexpected skips.
- Every reusable domain app has a documented standalone installation boundary. New sibling-app imports are prohibited; existing cross-app coupling is inventoried and tracked for removal through explicit integration adapters.
- Generated content, including firmware-related blog posts and tags, is produced idempotently by its owning service and has duplicate, failure, and state-transition tests; automated posts list only staff-approved firmware.
- Regression fixes identify the root cause. No repeated symptom-patch loop, blind retry/reversal, placeholder implementation, or untested compensating action is accepted.
- Documentation and requirement-to-test traceability reflect the actual implementation and clearly identify gaps.
- No unrelated user changes are removed, and no dependency or frontend stack is added without approval.

## Frontend and admin gate

The product owner has approved the initial frontend direction and stack: Django templates, Bootstrap, HTMX, Alpine's CSP-safe runtime, Lucide, and Vite. The user-facing frontend and admin panel share one hybrid, centralized design system and canonical shared static asset source while retaining distinct shells. App-specific additions are namespaced extensions, never copies of shared assets. Final visual identity and detailed design calibration remain subject to review. Large design files are split at the repository file budgets.

## Completion report

State exact commands and pass/fail/skip/unavailable results. Report remaining coverage gaps, environment limits, security/data risks, and any unapproved decision. A green test run alone does not establish exhaustive feature coverage.
