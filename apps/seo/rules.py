"""Catalogue of SEO standards: one place for thresholds, labels and fix semantics.

``SCAN_RULES`` are checked by the in-process content scan (``apps.seo.scan``);
``CRAWL_RULES`` are produced by the render crawl and the sitemap audit. The issues
board reads labels and the standard text from here so every finding explains which
rule it broke and what the target is.
"""

from __future__ import annotations

from dataclasses import dataclass

TITLE_MIN, TITLE_MAX = 25, 60
DESCRIPTION_MIN, DESCRIPTION_MAX = 70, 160
THIN_POST_WORDS = 150
THIN_PAGE_WORDS = 80
SLUG_MAX_LENGTH = 75

GROUPS: tuple[tuple[str, str], ...] = (
    ("posts", "Posts"),
    ("firmware", "Firmware"),
    ("models", "Models"),
    ("brands", "Brands"),
    ("chipsets", "Chipsets"),
    ("tools", "Tools & guides"),
    ("pages", "Pages & tags"),
    ("site", "Site-wide"),
)
GROUP_LABELS = dict(GROUPS)

GROUP_BY_PAGE_TYPE: dict[str, str] = {
    "blog_post": "posts",
    "blog_list": "posts",
    "blog_category": "posts",
    "blog_archive": "posts",
    "blog_brand": "posts",
    "blog_model": "posts",
    "firmware": "firmware",
    "firmware_category": "firmware",
    "firmware_browse": "firmware",
    "model": "models",
    "brand": "brands",
    "brand_list": "brands",
    "chipset": "chipsets",
    "chipset_list": "chipsets",
    "tool": "tools",
    "tools_hub": "tools",
    "guides_hub": "tools",
    "page": "pages",
    "pages_index": "pages",
    "tag": "pages",
    "tag_list": "pages",
}

OBJECT_GROUPS: dict[str, str] = {
    "blog.post": "posts",
    "blog.category": "posts",
    "firmwares.model": "models",
    "firmwares.brand": "brands",
    "firmwares.chipset": "chipsets",
    "firmwares.flashingtool": "tools",
    "pages.page": "pages",
    "tags.tag": "pages",
}


@dataclass(frozen=True)
class Rule:
    code: str
    label: str
    standard: str
    family: str
    fixable: bool = False


def _rules(*rules: Rule) -> dict[str, Rule]:
    return {rule.code: rule for rule in rules}


SCAN_RULES: dict[str, Rule] = _rules(
    Rule(
        "title_missing",
        "Page has no specific title",
        "Every indexable page needs its own title derived from the page content.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "title_too_short",
        "Title is too short",
        f"Titles of at least {TITLE_MIN} characters describe the page.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "title_too_long",
        "Title is too long",
        f"Titles over {TITLE_MAX} characters are cut off in search results.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "title_duplicate",
        "Title shared by several pages",
        "Each indexable page needs a unique title.",
        "metadata",
    ),
    Rule(
        "description_missing",
        "Page uses a generic description",
        "Every indexable page needs a description written from its own content.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "description_too_short",
        "Description is too short",
        f"Descriptions of {DESCRIPTION_MIN}-{DESCRIPTION_MAX} characters use the "
        "search snippet well.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "description_truncated",
        "Description is cut off",
        f"Descriptions over {DESCRIPTION_MAX} characters are truncated.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "description_duplicate",
        "Description shared by several pages",
        "Each indexable page needs a unique description.",
        "metadata",
    ),
    Rule(
        "canonical_not_self",
        "Canonical points elsewhere",
        "An indexable page should declare itself canonical unless it is a duplicate.",
        "metadata",
    ),
    Rule(
        "noindex_public_content",
        "Public content is set to noindex",
        "Live content should be indexable unless it is deliberately thin or private.",
        "metadata",
        fixable=True,
    ),
    Rule(
        "thin_page",
        "Page is excluded as thin",
        "Pages below the configured content minimum are kept out of the index.",
        "content",
    ),
    Rule(
        "social_image_missing",
        "No social preview image",
        "Open Graph and Twitter cards need an absolute image URL.",
        "metadata",
    ),
    Rule(
        "schema_missing",
        "No structured data",
        "Content pages should emit JSON-LD that matches their type.",
        "schema",
    ),
    Rule(
        "schema_invalid",
        "Structured data is incomplete",
        "Every JSON-LD node needs @context, @type and its required properties.",
        "schema",
    ),
    Rule(
        "slug_quality",
        "URL is not clean",
        f"Lowercase words separated by single hyphens, under {SLUG_MAX_LENGTH} "
        "characters.",
        "structure",
    ),
    Rule(
        "sitemap_url_unresolvable",
        "Sitemap URL has no route",
        "Every sitemap URL must resolve to a page.",
        "technical",
    ),
    Rule(
        "post_thin_content",
        "Post has little text",
        f"Published posts should carry at least {THIN_POST_WORDS} words of content.",
        "content",
    ),
    Rule(
        "post_images_missing_alt",
        "Images in the post lack alt text",
        "Every informative image needs a descriptive alt attribute.",
        "content",
    ),
    Rule(
        "post_body_h1",
        "Post body contains an <h1>",
        "The page title is the only <h1>; use <h2> and below in the body.",
        "content",
    ),
    Rule(
        "post_no_summary",
        "Post has no summary",
        "A summary feeds descriptions, cards and feeds.",
        "content",
    ),
    Rule(
        "duplicate_content",
        "Content duplicates another page",
        "Pages should not repeat the opening text of another page.",
        "content",
    ),
    Rule(
        "page_thin_content",
        "Page has little text",
        f"Pages should carry at least {THIN_PAGE_WORDS} words.",
        "content",
    ),
    Rule(
        "model_no_chipset",
        "Model has no chipset",
        "Models need a linked chipset to connect with flashing tools and guides.",
        "links",
    ),
    Rule(
        "model_missing_flash_guide",
        "No 'how to flash' guide for this model",
        "Every model with firmware needs a published flashing guide.",
        "guides",
    ),
    Rule(
        "chipset_missing_flash_guide",
        "No flashing guide for this chipset",
        "Every chipset with devices needs a published flashing guide.",
        "guides",
    ),
    Rule(
        "chipset_no_tool",
        "No flashing tool supports this chipset",
        "A chipset guide must name at least one supported flashing tool.",
        "guides",
    ),
    Rule(
        "tool_unlinked",
        "Flashing tool is linked to no chipset or brand",
        "Tools need supported chipsets or brands so they can be interlinked.",
        "links",
    ),
    Rule(
        "brand_no_description",
        "Brand has no description",
        "A written brand description makes the page unique.",
        "content",
    ),
)

CRAWL_RULES: dict[str, Rule] = _rules(
    *(
        Rule(code, label, standard, "technical")
        for code, label, standard in (
            ("fetch_failed", "Page could not be fetched", "Pages must respond."),
            (
                "robots_blocked_in_sitemap",
                "Sitemap URL blocked by robots.txt",
                "Sitemap URLs must be crawlable.",
            ),
            (
                "redirect_chain",
                "Redirect chain",
                "Redirects should reach the target in one hop.",
            ),
            (
                "sitemap_url_redirects",
                "Sitemap URL redirects",
                "Sitemaps list final URLs only.",
            ),
            (
                "sitemap_url_error",
                "Sitemap URL returns an error",
                "Sitemap URLs must return 200.",
            ),
            ("slow_response", "Slow response", "Respond in under 3 seconds."),
            ("missing_title", "No <title> in the HTML", "One <title> per page."),
            ("multiple_titles", "More than one <title>", "One <title> per page."),
            (
                "missing_description",
                "No meta description in the HTML",
                "One meta description per page.",
            ),
            (
                "multiple_descriptions",
                "More than one meta description",
                "One meta description per page.",
            ),
            (
                "multiple_robots_meta",
                "More than one robots meta tag",
                "One robots meta tag.",
            ),
            (
                "multiple_canonicals",
                "More than one canonical",
                "One canonical link.",
            ),
            (
                "missing_canonical",
                "No canonical link",
                "Indexable pages declare a canonical.",
            ),
            (
                "canonical_not_absolute",
                "Canonical is not absolute",
                "Canonical URLs are absolute.",
            ),
            (
                "canonical_to_error",
                "Canonical returns an error",
                "Canonical targets return 200.",
            ),
            (
                "sitemap_canonical_mismatch",
                "Sitemap URL canonicalises elsewhere",
                "Sitemap URLs are their own canonical.",
            ),
            (
                "noindex_in_sitemap",
                "noindex page in the sitemap",
                "Sitemaps list indexable pages only.",
            ),
            ("missing_h1", "No <h1>", "One <h1> per page."),
            ("multiple_h1", "More than one <h1>", "One <h1> per page."),
            ("invalid_json_ld", "Invalid JSON-LD", "JSON-LD must parse."),
            (
                "images_missing_alt",
                "Images without alt",
                "Every image needs an alt attribute.",
            ),
            (
                "broken_internal_link",
                "Broken internal link",
                "Internal links must return 200.",
            ),
            (
                "redirect_internal_link",
                "Internal link redirects",
                "Link to the final URL.",
            ),
            ("duplicate_title", "Duplicate <title>", "Titles are unique."),
            (
                "duplicate_description",
                "Duplicate meta description",
                "Descriptions are unique.",
            ),
            (
                "head_mismatch",
                "Served head differs from the resolver",
                "Served and intended heads match.",
            ),
            (
                "inventory_conflict",
                "Sitemap conflicts with indexation policy",
                "Sitemap and policy agree.",
            ),
            ("site_url_missing", "SITE_URL is not configured", "Set SITE_URL."),
            (
                "orphan_page",
                "No other page links here",
                "Every indexable page needs an internal link pointing to it.",
            ),
        )
    )
)

ALL_RULES: dict[str, Rule] = {**CRAWL_RULES, **SCAN_RULES}
SCAN_CODES = frozenset(SCAN_RULES)
CRAWL_FIXABLE_CODES = frozenset({"redirect_chain"})


def rule_for(code: str) -> Rule:
    return ALL_RULES.get(code) or Rule(
        code, code.replace("_", " ").capitalize(), "", ""
    )
