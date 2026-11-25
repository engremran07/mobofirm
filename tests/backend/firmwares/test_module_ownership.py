"""Internal module ownership of ``apps.firmwares`` (F-023).

The app carries several responsibilities that share one set of tables. Until a split is
worth its cost, the seams are explicit and enforced here: every module belongs to exactly
one responsibility, and a module may import only the responsibilities its own one is
allowed to depend on. A new cross-responsibility import fails this test and has to be a
deliberate edit of :data:`ALLOWED`, in review. The same map is documented in
``docs/04-backend/firmware-catalog.md``.

* ``base`` - schema and shared primitives (models, typed references, constants).
* ``catalogue`` - brands, models, variants, chipsets: public queries, seeding, SEO feeds.
* ``access`` - who may be served a file: tiers, access changes, package periods and the
  concurrent-transfer cap with its leases.
* ``intake`` - uploads, moderation and the catalogue/firmware requests.
* ``serving`` - the download gate: tokens, identity, signed links, public pages, API.
* ``guides`` - flashing tools, tool categories and guide generation.
* ``automation`` - signals, Celery tasks and the automated blog posts.
* ``shortener`` - the external download link shortener.
* ``wiring`` - admin, URLs and app configuration (may import anything).
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[3] / "apps" / "firmwares"

OWNERS: dict[str, str] = {
    "base": (
        "models tracking_models access_change_models shortener_models references "
        "firmware_cleanup constants conf sources crypto variant_rules package_formats "
        "chipsets shortener_constants notifications"
    ),
    "catalogue": (
        "catalog catalog_lookup chipset_audit seeding seed_verification sitemaps "
        "search_providers seo_providers popularity"
    ),
    "access": (
        "access access_changes access_requests auto_feature package_history dialogs "
        "admin_access download_leases"
    ),
    "intake": (
        "services submissions forms views serializers catalog_requests catalog_review "
        "reward_events events user_views admin_requests admin_io views_catalog_updates "
        "catalog_update_proposals catalog_update_forms"
    ),
    "serving": "download_service download_identity public_views api",
    "guides": "guide_service tool_matcher tool_categories views_flashing",
    "automation": "blog_automation signal_handlers tasks",
    "shortener": (
        "shortener shortener_providers shortener_views admin_shortener urls_short"
    ),
    "wiring": "admin admin_catalog_updates urls apps",
}

# What each responsibility may import (besides itself). ``base`` depends on nothing but
# two deliberate lazy imports of the catalogue (see ``LAZY_BASE_EDGES``).
ALLOWED: dict[str, set[str]] = {
    "base": set(),
    "catalogue": {"base", "guides", "intake"},
    "access": {"base", "catalogue"},
    "intake": {"base", "catalogue", "access", "automation"},
    "serving": {"base", "catalogue", "access", "intake", "shortener"},
    "guides": {"base", "catalogue", "automation"},
    "automation": {"base", "catalogue", "access", "guides", "intake", "serving"},
    "shortener": {"base", "catalogue", "access"},
    "wiring": set(OWNERS) - {"wiring"},
}
LAZY_BASE_EDGES = {("references", "catalog"), ("shortener_models", "catalog")}

OWNER_OF = {
    module: responsibility
    for responsibility, modules in OWNERS.items()
    for module in modules.split()
}


def top_level_modules() -> set[str]:
    return {path.stem for path in PACKAGE.glob("*.py") if path.stem != "__init__"}


def internal_imports(module: str) -> set[str]:
    """Top-level ``apps.firmwares`` modules imported by ``module`` (also lazily)."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    known = top_level_modules()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 1:
            names = (
                [node.module.split(".")[0]]
                if node.module
                else [alias.name for alias in node.names]
            )
        elif node.level == 0 and (node.module or "").startswith("apps.firmwares."):
            names = [(node.module or "").split(".")[2]]
        else:
            continue
        found.update(name for name in names if name in known)
    return found


def test_every_module_has_exactly_one_owner() -> None:
    owned = [module for modules in OWNERS.values() for module in modules.split()]

    assert len(owned) == len(set(owned)), "a module is listed under two owners"
    assert top_level_modules() == set(owned), (
        "classify new modules (or drop removed ones) in OWNERS: "
        f"{sorted(top_level_modules() ^ set(owned))}"
    )


def test_modules_import_only_what_their_responsibility_may_depend_on() -> None:
    violations = []
    for module in sorted(top_level_modules()):
        mine = OWNER_OF[module]
        for target in sorted(internal_imports(module)):
            theirs = OWNER_OF[target]
            if theirs == mine or theirs in ALLOWED[mine]:
                continue
            if mine == "base" and (module, target) in LAZY_BASE_EDGES:
                continue
            violations.append(f"{module} ({mine}) -> {target} ({theirs})")

    assert not violations, "cross-responsibility imports not allowed:\n" + "\n".join(
        violations
    )


def test_the_dependency_directions_form_the_documented_layers() -> None:
    """The serving gate depends on access, never the other way round."""
    assert "serving" not in ALLOWED["access"]
    assert "serving" not in ALLOWED["catalogue"]
    assert "intake" not in ALLOWED["access"]
    assert not ALLOWED["base"]


def test_the_catalogue_core_does_not_know_downloads_or_intake() -> None:
    for module in ("catalog", "catalog_lookup", "chipset_audit", "popularity"):
        imported = {OWNER_OF[target] for target in internal_imports(module)}
        assert imported <= {"base", "catalogue"}, (module, imported)
