---
name: release-management
description: "Use when preparing a Mobofirm release, choosing a SemVer bump, updating CHANGELOG.md, validating version metadata, creating a release tag, or operating the GitHub release workflow."
---
# Mobofirm Release Management

Use the canonical version in `pyproject.toml`; public and admin shells read it through `app/version.py` (see [releases.md](../../../docs/08-operations/releases.md) for the commands, surfaces and build metadata). Follow [release-management.md](../../../docs/09-delivery/release-management.md) for policy and repository settings.

## Prepare a release

1. Confirm product scope, acceptance evidence, release owner approval, and a clean worktree.
2. Add concise entries to `CHANGELOG.md` under `## [Unreleased]` and the appropriate Keep a Changelog category. Do not claim unverified behavior.
3. Run the focused tests, full CI-equivalent verification, frontend build/browser checks when applicable, and `scripts/release.py check`.
4. Commit the reviewed changelog entries. Release preparation intentionally requires a clean worktree.
5. Run the project venv's Python command: `.venv\Scripts\python.exe scripts\release.py bump patch` (or `minor`/`major`); add `--dry-run` first to preview, and `--commit`/`--tag` only when you intend the script to commit and tag.
6. Review the generated `pyproject.toml` and `CHANGELOG.md` diff and rerun required checks (the runtime reads `pyproject.toml`; `.venv\Scripts\python.exe -m pip install -e . --no-deps` refreshes local package metadata). Then commit the release metadata with the repository's required attribution.
7. Create and push an annotated `vMAJOR.MINOR.PATCH` tag for that commit only after the Problems panel and required checks are clear. The tag-triggered CI reruns the test job; only its success creates the GitHub release from the matching changelog section.
8. Verify the release page and deployed health/readiness and record rollout or rollback evidence in the approved release system.

## Bump selection

- `patch`: backward-compatible fixes and maintenance.
- `minor`: backward-compatible features.
- `major`: approved incompatible public behavior or API changes.

Do not treat database migrations by themselves as a major-version signal. Follow the compatibility and migration plan.

## Recovery

Never rewrite a published tag to repair a release. If validation or deployment fails, keep the release trace intact, document the failure, and issue a forward fix with a new version. Schema recovery must follow the reviewed migration rollback or forward-fix plan; do not run ad hoc production migrations.
