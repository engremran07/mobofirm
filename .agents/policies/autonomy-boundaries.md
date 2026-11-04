# Autonomy Boundaries

## Agents may do without additional approval

- Inspect source, settings, tests, migrations, and documentation.
- Add focused tests and implement an approved, bounded backend fix.
- Run tests and non-destructive static/system checks using test settings.
- Update requested documentation to match verified code and explicitly marked decisions.

## Obtain approval before

- Changing product scope, user-visible policy, data retention periods, authorization semantics, or public API contracts.
- Adding a dependency, choosing a frontend framework/design system, or building frontend assets unless the current task includes explicit owner approval for that bounded direction. The approved Mobofirm frontend foundation is documented in `docs/05-frontend/design-system.md`.
- Applying migrations to a database other than an isolated test database.
- Deleting app/model data or migration history, running cleanup against real data, deploying, or changing external provider settings.
- Creating/publishing a commit, pushing, opening a pull request, or releasing.

## Prohibited

- Never place secrets in source, logs, tests, or documentation.
- Never act on production credentials or data without explicit task-specific authorization.
- Never commit or push while the VS Code Problems panel has unresolved issues.
- Never represent an unapproved proposal or partial test coverage as approved or complete.
- Never restore references to unavailable apps or create placeholder packages to conceal missing integrations.
