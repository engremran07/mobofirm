"""Which fields repair which finding.

Every finding has at least one inline fix: the SEO override layer accepts every public
URL (a content object, or the route itself for pages without one), so the screen can
always edit the title, description, canonical, robots and social fields. This table
only decides which fields are *highlighted* for a finding and which object-level
repairs (slug, summary, alt text, headings) and console edit links go with it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    overrides: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    alts: bool = False
    h1: bool = False
    # Object fields only the owner's own edit form can change (opened with focus).
    console: tuple[str, ...] = ()
    guides: bool = False


TITLE = Plan(overrides=("meta_title",))
DESCRIPTION = Plan(overrides=("meta_description",))
CANONICAL = Plan(overrides=("canonical_url",))
INDEXING = Plan(overrides=("noindex", "robots_directives"))
SOCIAL = Plan(overrides=("social_image", "social_title", "social_description"))

PLANS: dict[str, Plan] = {
    **dict.fromkeys(
        ("title_missing", "title_too_short", "title_too_long", "title_duplicate"),
        TITLE,
    ),
    **dict.fromkeys(
        (
            "description_missing",
            "description_too_short",
            "description_truncated",
            "description_duplicate",
        ),
        DESCRIPTION,
    ),
    **dict.fromkeys(
        (
            "canonical_not_self",
            "missing_canonical",
            "multiple_canonicals",
            "canonical_not_absolute",
            "canonical_to_error",
            "sitemap_canonical_mismatch",
        ),
        CANONICAL,
    ),
    **dict.fromkeys(
        (
            "noindex_public_content",
            "noindex_in_sitemap",
            "thin_page",
            "inventory_conflict",
            "indexable_route_missing_from_sitemap",
            "robots_blocked_in_sitemap",
        ),
        INDEXING,
    ),
    "social_image_missing": SOCIAL,
    "discover_no_image": Plan(overrides=("social_image",), console=("hero_image",)),
    "discover_image_small": Plan(overrides=("social_image",), console=("hero_image",)),
    "discover_max_image_preview": Plan(overrides=("robots_directives",)),
    "discover_article_schema": Plan(console=("body",)),
    "discover_dates": Plan(console=("publish_at",)),
    "discover_author": Plan(console=("author",)),
    "schema_missing": Plan(overrides=("social_image",), console=("summary",)),
    "schema_invalid": Plan(overrides=("social_image",), console=("summary",)),
    "invalid_json_ld": Plan(overrides=("social_image",), console=("summary",)),
    "slug_quality": Plan(objects=("slug",)),
    "post_thin_content": Plan(console=("body", "content")),
    "page_thin_content": Plan(console=("content",)),
    "duplicate_content": Plan(console=("body", "content", "description")),
    "post_images_missing_alt": Plan(alts=True),
    "images_missing_alt": Plan(alts=True),
    "post_body_h1": Plan(h1=True),
    "multiple_h1": Plan(h1=True),
    "missing_h1": Plan(console=("title", "name")),
    "post_no_summary": Plan(objects=("summary",)),
    "brand_no_description": Plan(objects=("description",)),
    "model_no_chipset": Plan(console=("chipset_ref",)),
    "tool_unlinked": Plan(console=("chipsets", "supported_brands")),
    "chipset_no_tool": Plan(console=("flashing_tools",), guides=True),
    "model_missing_flash_guide": Plan(guides=True),
    "chipset_missing_flash_guide": Plan(guides=True),
    "orphan_page": Plan(console=("body", "content")),
    "broken_internal_link": Plan(console=("body", "content")),
    "redirect_internal_link": Plan(console=("body", "content")),
    "head_mismatch": Plan(
        overrides=("meta_title", "meta_description", "canonical_url")
    ),
}

DEFAULT_PLAN = Plan(overrides=("meta_title", "meta_description", "canonical_url"))


def plan_for(code: str) -> Plan:
    return PLANS.get(code, DEFAULT_PLAN)
