---
description: "Use when changing Mobofirm version metadata, changelog entries, release scripts, CI release jobs, or Git tags."
applyTo:
  - "pyproject.toml"
  - "CHANGELOG.md"
  - "scripts/release.py"
  - ".github/workflows/ci.yml"
---
# Release Management

- Treat `[project].version` in `pyproject.toml` as the sole application release version. Do not duplicate it in Django settings, templates, or app-specific configuration.
- Follow Semantic Versioning (`MAJOR.MINOR.PATCH`) for stable releases and Keep a Changelog categories for notes.
- Record user-visible and operational changes under `## [Unreleased]` before preparing a release. Keep notes factual and categorized.
- Use `.venv\Scripts\python.exe scripts\release.py check` on Windows or `.venv/bin/python scripts/release.py check` on Linux/macOS.
- Prepare a release only from a clean Git worktree with categorized, non-empty notes: `python scripts/release.py bump major|minor|patch`. Review both generated changes; the command never commits, tags, or pushes.
- A release tag must be `v<version>` and exactly match `pyproject.toml`. CI runs the full test job before creating the GitHub release from that version's changelog section.
- Never move or force-update a published tag. Correct an already-published release with a forward version and documented correction.
- Do not publish while the VS Code Problems panel has unresolved diagnostics or required CI checks have failed. Repository owner approval is required for commits and pushes.
