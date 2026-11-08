# Releases, versioning and build metadata

Operational runbook for the release flow. Policy and approval gates live in `docs/09-delivery/release-management.md`; this page describes how the version is defined, where it is shown, and how `scripts/release.py` operates.

## One source of truth

| Concern | Owner |
| --- | --- |
| Release version | `pyproject.toml` `[project].version` (SemVer, `MAJOR.MINOR.PATCH`). Only `scripts/release.py bump` edits it. |
| Release notes | `CHANGELOG.md` (`## [Unreleased]`, then dated `## [X.Y.Z] - YYYY-MM-DD` sections, Keep a Changelog categories). |
| Release marker | Annotated git tag `vX.Y.Z` that equals the pyproject version (created by `release.py tag`/`bump --tag`, pushed by the owner). |
| Runtime access | `app/version.py` — the only module that reads the version at runtime. |

`app/version.py` reads `[project].version` from the checkout's `pyproject.toml` when it exists (an editable install's package metadata is frozen when `pip install -e .` ran and goes stale after a bump) and falls back to the installed `mobofirm` distribution metadata when there is no checkout (wheel deployments). If neither exists it raises an explicit `RuntimeError`; it never invents a version. The Docker image copies `pyproject.toml`, so containers report the same value.

### Build metadata (optional, never fatal)

| Field | Source |
| --- | --- |
| git short SHA (7 chars) | `APP_GIT_SHA`, else `git log -1` of `HEAD` in a git checkout, else unknown |
| build date | `APP_BUILD_DATE` (ISO date or timestamp), else the HEAD commit date in a git checkout, else unknown |
| environment label | `APP_ENVIRONMENT` (`staging`, `qa`, …); unset/`production` = Production; `DEBUG` = Development |

Invalid values are ignored with a warning. Missing `git`, a missing `.git` directory or a git timeout leave the fields unknown; they never raise. `docker compose build` passes `APP_GIT_SHA`/`APP_BUILD_DATE` from the shell as build args (`export APP_GIT_SHA=$(git rev-parse --short=7 HEAD) APP_BUILD_DATE=$(date -u +%F)`); CI does the same and verifies the image reports the pyproject version.

## Where the version is shown

| Surface | What |
| --- | --- |
| Public footer | `Version X.Y.Z · build abc1234 (date)` (`data-app-version`) |
| `<meta name="application-version">` | `X.Y.Z` |
| Admin header | `vX.Y.Z · abc1234`, environment badge, theme switcher |
| Admin dashboard | Version, build, environment badge strip |
| Admin sidebar branding | `vX.Y.Z · abc1234` (Unfold `SITE_VERSION`) |
| `GET /health/live/` | `{"status": "ok", "version": "X.Y.Z"}` |
| `GET /health/ready/` | `{"status": "ok", "version": "X.Y.Z", "build": {"git_sha": …, "date": …}}`; 503 bodies stay `{"status": "unavailable"}` |
| `X-App-Version` response header | `X.Y.Z` on every response (`AppVersionHeaderMiddleware`) |

`tests/backend/core/test_release_version.py` fails when any surface disagrees, and proves that a changed `pyproject.toml` version reaches all of them.

## `scripts/release.py`

Run with the project interpreter (`.venv\Scripts\python.exe scripts\release.py …`).

| Command | Effect |
| --- | --- |
| `check` | Validate pyproject version, changelog structure, categories, dates, duplicate sections and compare links. Read-only. |
| `version` | Print the canonical version. |
| `bump major\|minor\|patch [--dry-run]` | Validate, then write the new version into `pyproject.toml` and move `[Unreleased]` into `## [X.Y.Z] - <date>` with a fresh empty `[Unreleased]` and regenerated compare links. **Files only**: no commit, tag or push. |
| `bump … --commit` | Also commit exactly `pyproject.toml` + `CHANGELOG.md` as `Release vX.Y.Z` (clean worktree required). |
| `bump … --commit --tag` | Also create the annotated tag `vX.Y.Z` on that commit. `--tag` without `--commit` is refused. |
| `bump … --allow-dirty` | Skip the clean-worktree check (files only; cannot be combined with `--commit`/`--tag`) when someone else commits the result. |
| `tag [--dry-run]` | Create the annotated tag for the current version on `HEAD`. Idempotent: succeeds if the tag already points at `HEAD`; refuses a tag on another commit, a dirty worktree, or a version without a changelog section. |
| `validate-tag vX.Y.Z` / `notes vX.Y.Z` | Used by the CI release job. |

Options: `--date YYYY-MM-DD` (default today, UTC), `--repo-url https://host/owner/repo` (default: derived from `origin`). The script never pushes. `bump` is safe to retry: it refuses empty or uncategorised `[Unreleased]` notes, an existing tag for the target version, and writes both files atomically (the first is restored if the second fails). The compare-link block is regenerated from the sections, so re-running it never duplicates entries.

### Release procedure

1. Keep `CHANGELOG.md` `[Unreleased]` current while working.
2. `release.py check`, then `release.py bump <level> --dry-run` to review the plan.
3. Commit the notes, run `release.py bump <level> --commit --tag` (or without flags and commit by hand).
4. `pip install -e . --no-deps` to refresh local package metadata (optional; the runtime reads `pyproject.toml`).
5. After owner approval push the commit and the tag. CI validates the tag against pyproject and the changelog, then publishes the GitHub release from that section.
6. Deploy via the approved path and confirm `/health/ready/` reports the new `version`.
