#!/usr/bin/env python3
"""Run the repo's closed-loop change governance gate.

This validates that a changed target is owned by an app, has impact traceability,
related tests, and no skipped or expected-failure markers in the relevant evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scripts.application_map import ROOT
from scripts.feature_impact import build_feature_impact_report

SKIP_MARKERS = (
    "pytest.mark.skip",
    "pytest.mark.xfail",
    ".skip(",
    ".xfail(",
    "skip(",
    "xfail(",
    "@skip",
    "@xfail",
)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _relevant_tests_for_owner(owner_app: str | None, root: Path) -> list[str]:
    if owner_app is None:
        return []
    tests_root = root / "tests" / "backend" / owner_app
    if not tests_root.exists():
        return []
    return sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in tests_root.rglob("test_*.py")
        if path.is_file()
    )


def _skipped_test_markers(test_path: str, root: Path) -> list[str]:
    full = root / test_path
    if not full.exists():
        return []
    content = _read_text(full)
    markers = [marker for marker in SKIP_MARKERS if marker in content]
    return markers


def run_governance_audit(target: str, root: Path = ROOT) -> dict[str, Any]:
    """Audit ownership, impact traceability and test evidence for ``target``."""
    impact = build_feature_impact_report(target, root)
    owner_app = impact.get("owner_app")

    violations: list[str] = []
    if owner_app is None:
        violations.append("No owning Django app could be identified for the target.")

    relevant_tests = _relevant_tests_for_owner(owner_app, root)
    if not relevant_tests:
        owner = owner_app or "unknown"
        violations.append(
            f"No app-owned backend tests were found under tests/backend/{owner}"
        )

    integration_tests = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "tests" / "backend" / "integrations").glob("test_*.py")
        if path.is_file()
    )

    skipped_tests: list[str] = []
    for test_path in relevant_tests + integration_tests:
        markers = _skipped_test_markers(test_path, root)
        if markers:
            skipped_tests.append(f"{test_path}: {', '.join(markers[:3])}")

    if skipped_tests:
        violations.append(
            "Skipped or xfail evidence is present in the relevant test set: "
            + "; ".join(skipped_tests)
        )

    if impact["summary"]["route_count"] and not relevant_tests:
        violations.append(
            "The target maps to routes but no owning tests are present to prove "
            "the behavior."
        )

    status = "pass" if not violations else "fail"
    return {
        "target": target,
        "status": status,
        "owner_app": owner_app,
        "impact": impact,
        "required_evidence": {
            "owner_tests": relevant_tests,
            "integration_tests": integration_tests,
            "skipped_tests": skipped_tests,
        },
        "violations": violations,
    }


def main(argv: list[str] | None = None) -> int:
    """Run the audit from the command line; exit non-zero when it fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target", required=True, help="Target file, app, or template to audit"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, help="Write the audit JSON to this path")
    args = parser.parse_args(argv)

    try:
        audit = run_governance_audit(args.target, args.root.resolve())
        serialized = json.dumps(audit, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
        return 0 if audit["status"] == "pass" else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"governance-audit: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
