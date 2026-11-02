# Connected Django Apps

These four packages (`blog`, `comments`, `firmwares`, and `tags`) are related app modules, not a complete Django project in this checkout. Preserve their existing import and integration conventions; do not invent missing project settings, dependencies, frontend assets, or mobile clients.

- Read [the engineering workflow](../docs/engineering-workflow.md) before changing architecture or test strategy.
- Every user-visible feature or bug fix needs a focused regression test; cross-app behavior also needs a contract/integration test.
- Keep changes inside the owning app unless a real integration boundary requires otherwise. Prefer existing models, services, tasks, signals, and test patterns over parallel abstractions.
- Make migrations reversible and review their generated operations. Never change data, purge environments/logs, or weaken tests as a shortcut.
- Report exactly which checks ran, their outcomes, and any checks blocked by the missing Django project or optional toolchain.
- Load the relevant workflow skill: `django-feature-loop`, `inter-app-automation`, `frontend-stack`, `observability-retention`, or `deep-verification`.
