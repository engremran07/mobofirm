#!/usr/bin/env python3
"""Validate the repository governance pack as a single closed-loop system.

The governance pack is the combination of required policy docs,
agent instructions, reusable workflow skills, specialized agent roles,
quality gates, and test-enforced evidence. This script ensures the
repository includes each required layer and surfaces missing pieces
before an implementation can claim a clean governance baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DOCUMENTS = {
    "AGENTS.md": ROOT / "AGENTS.md",
    "docs/00-governance/definition-of-done.md": ROOT
    / "docs"
    / "00-governance"
    / "definition-of-done.md",
    "docs/00-governance/registry.yaml": ROOT
    / "docs"
    / "00-governance"
    / "registry.yaml",
    "docs/00-governance/policies/quality-gates.yaml": ROOT
    / "docs"
    / "00-governance"
    / "policies"
    / "quality-gates.yaml",
    "docs/00-governance/policies/agent-autonomy.yaml": ROOT
    / "docs"
    / "00-governance"
    / "policies"
    / "agent-autonomy.yaml",
    ".agents/policies/source-of-truth.yaml": ROOT
    / ".agents"
    / "policies"
    / "source-of-truth.yaml",
}

REQUIRED_INSTRUCTIONS = {
    "django-python": ROOT
    / ".github"
    / "instructions"
    / "django-python.instructions.md",
    "frontend": ROOT / ".github" / "instructions" / "frontend.instructions.md",
    "tests": ROOT / ".github" / "instructions" / "tests.instructions.md",
    "release-management": ROOT
    / ".github"
    / "instructions"
    / "release-management.instructions.md",
}

REQUIRED_SKILLS = {
    "django-feature-loop": ROOT
    / ".github"
    / "skills"
    / "django-feature-loop"
    / "SKILL.md",
    "frontend-stack": ROOT / ".github" / "skills" / "frontend-stack" / "SKILL.md",
    "deep-verification": ROOT / ".github" / "skills" / "deep-verification" / "SKILL.md",
    "secure-form-workflow": ROOT
    / ".github"
    / "skills"
    / "secure-form-workflow"
    / "SKILL.md",
    "inter-app-automation": ROOT
    / ".github"
    / "skills"
    / "inter-app-automation"
    / "SKILL.md",
}

REQUIRED_AGENTS = {
    "django-feature-engineer": ROOT
    / ".github"
    / "agents"
    / "django-feature-engineer.agent.md",
    "verification-engineer": ROOT
    / ".github"
    / "agents"
    / "verification-engineer.agent.md",
}

REQUIRED_RUNNABLE_GATES = {
    "scripts/application_map.py": ROOT / "scripts" / "application_map.py",
    "scripts/feature_impact.py": ROOT / "scripts" / "feature_impact.py",
    "scripts/governance_audit.py": ROOT / "scripts" / "governance_audit.py",
    "tests/backend/core/test_application_map.py": ROOT
    / "tests"
    / "backend"
    / "core"
    / "test_application_map.py",
    "tests/backend/core/test_feature_impact.py": ROOT
    / "tests"
    / "backend"
    / "core"
    / "test_feature_impact.py",
    "tests/backend/core/test_governance_audit.py": ROOT
    / "tests"
    / "backend"
    / "core"
    / "test_governance_audit.py",
}


def _summarize_files(mapping: dict[str, Path]) -> dict[str, bool]:
    return {name: path.exists() for name, path in mapping.items()}


def build_governance_pack(root: Path = ROOT) -> dict[str, Any]:
    """Report which governance documents, instructions, skills and agents exist."""
    docs = _summarize_files(
        {
            key: root / key.replace("/", "\\").replace("\\", "/")
            for key in REQUIRED_DOCUMENTS
        }
    )
    # normalize repository-relative dependencies without weird path rewriting
    docs = {
        "AGENTS.md": (root / "AGENTS.md").exists(),
        "docs/00-governance/definition-of-done.md": (
            root / "docs" / "00-governance" / "definition-of-done.md"
        ).exists(),
        "docs/00-governance/registry.yaml": (
            root / "docs" / "00-governance" / "registry.yaml"
        ).exists(),
        "docs/00-governance/policies/quality-gates.yaml": (
            root / "docs" / "00-governance" / "policies" / "quality-gates.yaml"
        ).exists(),
        "docs/00-governance/policies/agent-autonomy.yaml": (
            root / "docs" / "00-governance" / "policies" / "agent-autonomy.yaml"
        ).exists(),
        ".agents/policies/source-of-truth.yaml": (
            root / ".agents" / "policies" / "source-of-truth.yaml"
        ).exists(),
    }
    instructions = _summarize_files(REQUIRED_INSTRUCTIONS)
    skills = _summarize_files(REQUIRED_SKILLS)
    agents = _summarize_files(REQUIRED_AGENTS)
    enforcement = _summarize_files(REQUIRED_RUNNABLE_GATES)

    missing = []
    for section, payload in (
        ("docs", docs),
        ("instructions", instructions),
        ("skills", skills),
        ("agents", agents),
        ("enforcement", enforcement),
    ):
        for key, present in payload.items():
            if not present:
                missing.append(f"{section}:{key}")

    status = "pass" if not missing else "fail"
    return {
        "schema_version": 1,
        "status": status,
        "missing": missing,
        "docs": docs,
        "instructions": instructions,
        "skills": skills,
        "agents": agents,
        "enforcement": enforcement,
        "closed_loop_definition": {
            "policy": "docs and governance decisions define the required controls",
            "workflow": "instructions and skills encode the operating procedure",
            "specialization": "agents own bounded responsibilities and evidence",
            "enforcement": (
                "scripts and tests fail when ownership, evidence, or skip handling "
                "is missing"
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    """Write the governance-pack report; exit non-zero when anything is missing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--output", type=Path, help="write the governance-pack report to this path"
    )
    args = parser.parse_args(argv)

    try:
        report = build_governance_pack(args.root.resolve())
        serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
        return 0 if report["status"] == "pass" else 1
    except (OSError, ValueError, RuntimeError) as error:
        print(f"governance-pack: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
