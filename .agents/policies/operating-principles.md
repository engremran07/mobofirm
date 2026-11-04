# Agent Operating Principles

1. **Evidence before conclusions.** Inspect the current tree, project settings, migrations, tests, and callers. Separate observed facts from assumptions and unapproved proposals.
2. **Own the change.** Each domain app owns its models, persistence, services, and lifecycle and must be installable without sibling domain apps. Preserve public contracts; place optional cross-app workflows in explicit integration adapters. Do not add direct sibling imports as shortcuts.
3. **Close the loop.** Every enabled feature must have an approved/traceable requirement, functional implementation, observable tests, and recorded verification evidence. Cross-app changes need a contract or integration test.
4. **Protect data.** Never use production data for routine tests, run destructive cleanup, or apply migrations to an unverified database. Review migration operations and rollback behavior.
5. **Minimize dependencies.** Reuse the configured stack. Add a dependency only when the approved requirement cannot be met with current tools and the approval matrix permits it.
6. **Keep frontend decisions gated.** Do not implement frontend or select a UI framework until design is approved. The approved target is one hybrid centralized frontend/admin design system and one canonical shared static asset source, with namespaced app extensions and no duplicate shared files.
7. **Fix causes, not symptoms.** Reproduce regressions and establish a causal chain before changing code. Do not repeat symptom patches or run unbounded retry/reversal loops. Define and test rollback/compensation behavior where a workflow can leave partial state.
8. **No fake completion.** Do not leave active placeholder/stub behavior, silent no-ops, or skipped feature tests. If the requirement is not implemented, explicitly disable it or track it as pending; never report it as functional.
9. **Be explicit about failure.** Do not hide errors behind broad catches, empty success responses, retries without a demonstrated transient condition, skipped tests, or weakened assertions.
10. **Respect the workspace.** Preserve existing user changes. Do not commit or push with unresolved VS Code Problems; do not publish without explicit authorization.
11. **Report the proof.** Name the commands run and their outcomes. Distinguish passed, failed, skipped, and unavailable checks.

Repository-specific gates and approval roles are in `../../docs/00-governance/policies/quality-gates.yaml` and `approval-matrix.yaml`.
