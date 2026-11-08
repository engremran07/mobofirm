# Release Management

## Versioning contract

- `pyproject.toml` `[project].version` is the only application release-version source. It follows Semantic Versioning (`MAJOR.MINOR.PATCH`); prerelease/build identifiers are valid metadata, but the release-preparation command currently creates stable releases only.
- Public and Django admin shells render the version from `app/version.py` (pyproject at runtime, installed metadata when there is no checkout), together with optional build metadata; see `docs/08-operations/releases.md`. Do not add another settings value.
- `CHANGELOG.md` follows Keep a Changelog. Record changes in `Unreleased` using `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, or `Security` headings and concise bullets.
- Git release tags use `vMAJOR.MINOR.PATCH`, must exactly match `pyproject.toml`, and are never moved or force-updated after publication.
- Blog post revision history is separate content versioning owned by the blog app. Restoring remains limited to drafts; it does not restore publication state.

## Prepare and publish

1. Confirm product approval, scope, acceptance evidence, release owner, migration/rollback plan, and a clean worktree. Resolve security and quality gates applicable to the change.
2. Update `CHANGELOG.md` while implementing the work. Run `.venv\Scripts\python.exe scripts\release.py check` (Windows) or `.venv/bin/python scripts/release.py check` (Linux/macOS).
3. Run focused tests and the CI-equivalent suite, Django and migration checks, dependency checks, and frontend/browser validation when affected. CI does not migrate or access production data.
4. Commit the reviewed changelog entries so the worktree is clean, then choose a bump:

   ```powershell
   .venv\Scripts\python.exe scripts\release.py bump patch
   ```

   Use `minor` for backward-compatible functionality and `major` only for an approved breaking public change. `bump` updates the canonical version and archives `Unreleased` notes under a dated release heading (with regenerated compare links). Preview with `--dry-run`. It refuses dirty worktrees (unless `--allow-dirty`), uncategorized/empty notes, and an existing local tag. By default it writes files only; `--commit` and `--commit --tag` are explicit opt-ins, and it never pushes. See `docs/08-operations/releases.md` for every command.

5. Review the generated diff, refresh the editable install (`.venv\Scripts\python.exe -m pip install -e .` on Windows), run `scripts/release.py check` and the required tests again, and commit the version/changelog update. Do not combine unrelated changes into a release commit.
6. Create an annotated tag on that commit and push it only after the Problems panel is clear and the repository owner authorizes publication:

   ```powershell
   git tag -a vX.Y.Z -m "Mobofirm X.Y.Z"
   git push origin vX.Y.Z
   ```

7. A pushed `v*` tag triggers the CI test job. The GitHub release job runs only after that job passes, verifies the tag against package metadata and changelog, and publishes the matching release notes. The workflow does not deploy the application.
8. Verify the GitHub release, then deploy through the approved deployment path. Confirm liveness/readiness and the affected user journey; record the release identifier, validation evidence, operator, and rollback/forward-fix outcome in the approved release system.

The public shell and admin metadata contract is covered by `tests/backend/core/test_release_metadata.py` and the cross-surface/pyproject-bump contract by `tests/backend/core/test_release_version.py`. The release CLI's clean-worktree, changelog, bump, link, commit, tag, and notes behavior is covered by `tests/backend/core/test_release_script.py`.

## CI and repository protections

`.github/workflows/ci.yml` validates release metadata on normal builds and gates tag publication on the complete CI test job. Configure repository settings to require the CI test check on `main`, disallow force pushes to protected release tags, and restrict release/publish permissions to trusted maintainers. Those repository-host settings are external to this checked-in workflow and must be verified by an owner; this document does not assert they are enabled.

CI checks formatting, the frontend build, Chromium browser tests, the isolated test suite, Django checks, migration consistency, and Python dependency consistency. It never migrates or accesses production data.

## Failure, rollback, and publication gates

- Never move or force-update a published tag. Correct a release by preparing a new forward version and explaining the correction in its changelog.
- If CI fails, do not publish. Correct the source or release metadata, rerun required checks, and create a new tag only for the validated commit.
- Review migration operations and rollback/forward-fix plans before production rollout. Apply migrations only through the approved deployment process against a verified target.
- Never commit or push while the VS Code Problems panel has unresolved issues. Explicit repository-owner authorization and passing required checks are also mandatory; a green test suite does not override the diagnostics gate.
