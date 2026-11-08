"""
Enterprise ad placement scanner — discovers ad-worthy locations in templates.

Three scanning modes:

1. **Template scan** (admin-triggered, synchronous):
   Deep-scans public HTML template files for content sections, viewport zones,
   template inheritance chains, and ad density. Creates ``ScanDiscovery``
   records in **pending** state for admin approval — never auto-creates
   ``AdPlacement`` records. Respects ``TemplateAdExclusion`` entries.

2. **Blog post content scan** (auto, per-post via Celery):
   Analyzes the HTML body of a published blog post to find paragraph breaks
   and determine optimal in-content ad insertion points.

3. **Discovery approval** (admin-triggered):
   Converts approved ``ScanDiscovery`` records into live ``AdPlacement`` entries.

Admin templates, error pages, and base layouts are **always excluded**.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.template.defaultfilters import slugify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Directories to EXCLUDE from template scanning (admin / infra / layouts)
# ---------------------------------------------------------------------------
EXCLUDED_DIRS: set[str] = {
    "admin",
    "admin_suite",
    "layouts",
    "base",
    "errors",
    "account",  # allauth account templates
}

# ---------------------------------------------------------------------------
# Public template directories we actively scan for ad placement potential
# ---------------------------------------------------------------------------
PUBLIC_DIRS: set[str] = {
    "blog",
    "firmwares",
    "devices",
    "pages",
    "bounty",
    "tags",
    "users",
    "components",
    "core",
    "ads",
    "consent",
    "distribution",
    "seo",
    "analytics",
}

# Regex: existing ad slot references in templates
AD_SLOT_PATTERN = re.compile(
    r"(?:ads:slot|<!--\s*ad-slot:|{%\s*render_ad_slot\s+['\"])(?P<name>[\w\-\s]+)",
    re.IGNORECASE,
)

# Django template tags for inheritance/include analysis
EXTENDS_PATTERN = re.compile(
    r'{%\s*extends\s+["\'](?P<parent>[^"\']+)["\']', re.IGNORECASE
)
INCLUDE_PATTERN = re.compile(
    r'{%\s*include\s+["\'](?P<partial>[^"\']+)["\']', re.IGNORECASE
)
BLOCK_PATTERN = re.compile(r"{%\s*block\s+(?P<name>\w+)\s*%}", re.IGNORECASE)

# Content-structure patterns (what makes a template suitable for ads)
CONTENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "article": re.compile(r"<article[^>]*>", re.IGNORECASE),
    "main_content": re.compile(
        r'class="[^"]*(?:content|main|body|prose)[^"]*"', re.IGNORECASE
    ),
    "sidebar": re.compile(r'class="[^"]*(?:sidebar|aside|rail)[^"]*"', re.IGNORECASE),
    "footer": re.compile(r"<footer[^>]*>", re.IGNORECASE),
    "list": re.compile(r'class="[^"]*(?:list|grid|feed|posts)[^"]*"', re.IGNORECASE),
    "hero": re.compile(r'class="[^"]*(?:hero|banner|jumbotron)[^"]*"', re.IGNORECASE),
    "card_grid": re.compile(
        r'class="[^"]*(?:card|tile)[^"]*".*class="[^"]*(?:grid|flex)[^"]*"',
        re.IGNORECASE | re.DOTALL,
    ),
    "navigation": re.compile(r"<nav[^>]*>", re.IGNORECASE),
    "form": re.compile(r"<form[^>]*>", re.IGNORECASE),
    "table": re.compile(r"<table[^>]*>", re.IGNORECASE),
}

# Patterns that reduce ad suitability (interactive/functional areas)
NEGATIVE_PATTERNS: dict[str, re.Pattern[str]] = {
    "forms": re.compile(r"<form[^>]*>", re.IGNORECASE),
    "login": re.compile(r"(?:login|signin|sign.in|authenticate)", re.IGNORECASE),
    "checkout": re.compile(r"(?:checkout|payment|billing)", re.IGNORECASE),
    "modal": re.compile(r'class="[^"]*modal[^"]*"', re.IGNORECASE),
}

# Viewport zone classification
ZONE_PATTERNS: dict[str, re.Pattern[str]] = {
    "above-fold": re.compile(
        r'class="[^"]*(?:hero|banner|header|jumbotron|masthead)[^"]*"', re.IGNORECASE
    ),
    "mid-content": re.compile(
        r'class="[^"]*(?:content|main|body|prose|article)[^"]*"', re.IGNORECASE
    ),
    "sidebar": re.compile(
        r'class="[^"]*(?:sidebar|aside|rail|widget)[^"]*"', re.IGNORECASE
    ),
    "footer": re.compile(r"<footer[^>]*>", re.IGNORECASE),
    "sticky": re.compile(r'class="[^"]*(?:sticky|fixed|pin)[^"]*"', re.IGNORECASE),
    "in-feed": re.compile(
        r'class="[^"]*(?:list|grid|feed|posts|items)[^"]*"', re.IGNORECASE
    ),
}

# ---------------------------------------------------------------------------
# Traffic estimation heuristics by template name patterns
# ---------------------------------------------------------------------------
HIGH_TRAFFIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:index|home|landing|list|search)", re.IGNORECASE),
    re.compile(r"pages/", re.IGNORECASE),
]
LOW_TRAFFIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:tag|archive|category|settings|profile)", re.IGNORECASE),
    re.compile(r"(?:terms|privacy|about|contact|faq)", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Template type classification
# ---------------------------------------------------------------------------
DETAIL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:detail|single|post|article|topic|view)", re.IGNORECASE),
]
LIST_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:list|index|archive|category|search|browse)", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Placement rules:  content_type → list of placements to create as discoveries
# Each entry = (suffix, zone, allowed_types, allowed_sizes, desc_tpl, confidence)
# ---------------------------------------------------------------------------
PLACEMENT_RULES: dict[str, list[tuple[str, str, str, str, str, float]]] = {
    "article": [
        (
            "in-article-top",
            "above-fold",
            "banner,native",
            "728x90,300x250",
            "Above-the-fold banner inside article content in {tpl}",
            85.0,
        ),
        (
            "in-article-mid",
            "mid-content",
            "native,html",
            "300x250,336x280",
            "Mid-article native ad between paragraphs in {tpl}",
            80.0,
        ),
    ],
    "main_content": [
        (
            "content-top",
            "above-fold",
            "banner,native,html",
            "728x90,970x250",
            "Top of main content area in {tpl}",
            75.0,
        ),
        (
            "content-bottom",
            "mid-content",
            "banner,native",
            "728x90,300x250",
            "Bottom of main content area in {tpl}",
            65.0,
        ),
    ],
    "sidebar": [
        (
            "sidebar-top",
            "sidebar",
            "banner,native",
            "300x250,300x600,160x600",
            "Top of sidebar in {tpl}",
            90.0,
        ),
        (
            "sidebar-sticky",
            "sticky",
            "banner",
            "300x250,300x600",
            "Sticky sidebar unit in {tpl}",
            70.0,
        ),
    ],
    "list": [
        (
            "in-feed",
            "in-feed",
            "native,html",
            "300x250,fluid",
            "In-feed ad between list/grid items in {tpl}",
            75.0,
        ),
    ],
    "footer": [
        (
            "above-footer",
            "footer",
            "banner,html",
            "728x90,970x90",
            "Leaderboard above footer in {tpl}",
            60.0,
        ),
    ],
    "hero": [
        (
            "after-hero",
            "above-fold",
            "banner",
            "970x250,728x90",
            "Banner directly after hero/banner section in {tpl}",
            70.0,
        ),
    ],
    "card_grid": [
        (
            "in-grid",
            "in-feed",
            "native",
            "fluid",
            "Native ad card injected into card grid in {tpl}",
            72.0,
        ),
    ],
}


# ===== 1. Template scan (admin-triggered, synchronous) ====================


def scan_templates_for_placements() -> dict[str, Any]:
    """
    Deep-scan public-facing HTML templates and create ``ScanDiscovery``
    records in **pending** state for admin review.

    Never auto-creates ``AdPlacement`` records. Respects
    ``TemplateAdExclusion`` entries. Analyses template inheritance,
    viewport zones, content structure, ad density, and traffic estimation.

    Returns dict with ``scanned``, ``discoveries``, ``excluded``, ``suggestions``.
    """
    from apps.ads.models import (
        AutoAdsScanResult,
        ScanDiscovery,
        TemplateAdExclusion,
    )

    templates_dir = Path(settings.BASE_DIR) / "templates"
    if not templates_dir.exists():
        return {"status": "error", "reason": "templates_dir_not_found"}

    # Load exclusion list upfront
    excluded_paths: set[str] = set(
        TemplateAdExclusion.objects.values_list("template_path", flat=True)
    )

    scanned = 0
    discoveries_created = 0
    excluded_count = 0
    suggestions: list[dict[str, Any]] = []

    for path in templates_dir.rglob("*.html"):
        relative = path.relative_to(templates_dir)
        top_dir = relative.parts[0] if len(relative.parts) > 1 else ""

        # Skip excluded directories
        if top_dir in EXCLUDED_DIRS:
            continue

        # Skip fragments (standalone HTMX snippets — too small for ads)
        if "fragments" in relative.parts:
            continue

        relative_path = relative.as_posix()

        # Skip admin-excluded templates
        if relative_path in excluded_paths:
            excluded_count += 1
            continue

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        scanned += 1

        # ── Template inheritance chain analysis ──
        extends_chain = _extract_extends_chain(text)
        includes_list = INCLUDE_PATTERN.findall(text)

        # ── Content structure analysis ──
        content_analysis: dict[str, int] = {}
        for pattern_name, pattern in CONTENT_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                content_analysis[pattern_name] = len(matches)

        # ── Negative signal detection (reduce score) ──
        negative_signals: dict[str, int] = {}
        for neg_name, neg_pattern in NEGATIVE_PATTERNS.items():
            neg_matches = neg_pattern.findall(text)
            if neg_matches:
                negative_signals[neg_name] = len(neg_matches)

        # ── Viewport zone detection ──
        viewport_zones = _detect_viewport_zones(text)

        # ── Existing ad slots count (ad density) ──
        existing_slots = len(AD_SLOT_PATTERN.findall(text))
        ad_density_warning = existing_slots >= 3

        # ── Classify template type ──
        template_type = _classify_template_type(relative_path, text)

        # ── Estimate traffic level ──
        estimated_traffic = _estimate_traffic(relative_path, template_type)

        # ── Calculate overall placement score ──
        score = _calculate_placement_score(
            content_analysis, negative_signals, existing_slots, estimated_traffic
        )

        if not content_analysis:
            # No content structure found — still log the scan but no discoveries
            AutoAdsScanResult.objects.update_or_create(
                template_path=relative_path,
                defaults={
                    "content_analysis": content_analysis,
                    "suggested_placements": [],
                    "score": score,
                    "template_type": template_type,
                    "estimated_traffic": estimated_traffic,
                    "viewport_zones": viewport_zones,
                    "ad_density_warning": ad_density_warning,
                    "extends_chain": extends_chain + includes_list,
                    "total_discoveries": 0,
                    "review_status": "pending",
                },
            )
            continue

        # ── Generate placement suggestions ──
        suggestion_list = _suggest_placements(content_analysis, estimated_traffic)

        # ── Create/update scan result ──
        scan_result, _ = AutoAdsScanResult.objects.update_or_create(
            template_path=relative_path,
            defaults={
                "content_analysis": content_analysis,
                "suggested_placements": suggestion_list,
                "score": score,
                "template_type": template_type,
                "estimated_traffic": estimated_traffic,
                "viewport_zones": viewport_zones,
                "ad_density_warning": ad_density_warning,
                "extends_chain": extends_chain + includes_list,
                "review_status": "pending",
            },
        )

        # ── Create individual ScanDiscovery records (pending state) ──
        tpl_slug = slugify(relative_path.replace("/", " ").replace(".html", ""))
        tpl_discoveries = 0

        for content_type in content_analysis:
            rules = PLACEMENT_RULES.get(content_type, [])
            for suffix, zone, allowed_types, allowed_sizes, desc_tpl, conf in rules:
                code = f"{tpl_slug}-{suffix}"
                name = f"{tpl_slug} {suffix}".replace("-", " ").title()

                # Skip if discovery already exists for this scan result + code
                if scan_result.discoveries.filter(placement_code=code).exists():
                    continue

                # Adjust confidence based on traffic + negative signals
                adjusted_conf = _adjust_confidence(
                    conf, estimated_traffic, negative_signals, ad_density_warning
                )

                ScanDiscovery.objects.create(
                    scan_result=scan_result,
                    placement_code=code,
                    placement_name=name,
                    zone=zone,
                    allowed_types=allowed_types,
                    allowed_sizes=allowed_sizes,
                    description=desc_tpl.format(tpl=relative_path),
                    confidence=adjusted_conf,
                    rationale=_build_rationale(
                        content_type, zone, estimated_traffic, adjusted_conf
                    ),
                    status="pending",
                )
                discoveries_created += 1
                tpl_discoveries += 1

        scan_result.total_discoveries = scan_result.discoveries.count()
        scan_result.save(update_fields=["total_discoveries"])

        suggestions.append(
            {
                "template": relative_path,
                "content_types": list(content_analysis.keys()),
                "zones": viewport_zones,
                "discoveries": tpl_discoveries,
                "score": float(score),
                "traffic": estimated_traffic,
                "type": template_type,
            }
        )

    logger.info(
        "Template scan complete. Scanned: %d, Discoveries: %d, Excluded: %d",
        scanned,
        discoveries_created,
        excluded_count,
    )

    return {
        "status": "success",
        "scanned": scanned,
        "discoveries": discoveries_created,
        "excluded": excluded_count,
        "suggestions": suggestions[:50],
    }


# ===== 2. Discovery approval (admin-triggered) ============================


def approve_discovery(discovery_id: int, user: Any) -> dict[str, Any]:
    """
    Approve a single ``ScanDiscovery`` and create the corresponding
    ``AdPlacement`` record.
    """
    from apps.ads.models import AdPlacement, ScanDiscovery

    try:
        discovery = ScanDiscovery.objects.select_related("scan_result").get(
            pk=discovery_id
        )
    except ScanDiscovery.DoesNotExist:
        return {"status": "error", "reason": "discovery_not_found"}

    if discovery.status != "pending":
        return {"status": "error", "reason": f"already_{discovery.status}"}

    from django.utils import timezone

    from apps.ads.zones import zone_for_suggestion

    slug = slugify(discovery.placement_code)
    scan = discovery.scan_result
    zone = zone_for_suggestion(discovery.placement_code)

    # Two suggestions of one template can map to the same layout zone; the zone is
    # filled once, so the later approval links to the placement that already exists.
    existing = (
        AdPlacement.objects.filter(
            template_reference=scan.template_path, zone=zone, is_deleted=False
        ).first()
        if zone
        else None
    )
    if existing is not None:
        placement, created = existing, False
    else:
        placement, created = AdPlacement.objects.get_or_create(
            slug=slug,
            defaults={
                "code": discovery.placement_code,
                "name": discovery.placement_name,
                "description": discovery.description,
                "allowed_types": discovery.allowed_types,
                "allowed_sizes": discovery.allowed_sizes,
                "context": (
                    scan.template_path.split("/")[0]
                    if "/" in scan.template_path
                    else "global"
                ),
                "page_context": scan.template_path,
                "template_reference": scan.template_path,
                "zone": zone,
                "is_enabled": True,
                "is_active": True,
            },
        )

    discovery.status = "approved"
    discovery.reviewed_by = user
    discovery.reviewed_at = timezone.now()
    discovery.placement = placement
    discovery.save(update_fields=["status", "reviewed_by", "reviewed_at", "placement"])

    # Update parent scan result status
    _update_scan_review_status(scan, user)

    return {
        "status": "success",
        "placement_id": placement.pk,
        "created": created,
    }


def reject_discovery(discovery_id: int, user: Any) -> dict[str, Any]:
    """Reject a single ``ScanDiscovery``."""
    from apps.ads.models import ScanDiscovery

    try:
        discovery = ScanDiscovery.objects.select_related("scan_result").get(
            pk=discovery_id
        )
    except ScanDiscovery.DoesNotExist:
        return {"status": "error", "reason": "discovery_not_found"}

    if discovery.status != "pending":
        return {"status": "error", "reason": f"already_{discovery.status}"}

    from django.utils import timezone

    discovery.status = "rejected"
    discovery.reviewed_by = user
    discovery.reviewed_at = timezone.now()
    discovery.save(update_fields=["status", "reviewed_by", "reviewed_at"])

    _update_scan_review_status(discovery.scan_result, user)

    return {"status": "success"}


def bulk_approve_discoveries(discovery_ids: list[int], user: Any) -> dict[str, Any]:
    """Approve multiple discoveries at once."""
    results = {"approved": 0, "errors": 0}
    for did in discovery_ids:
        result = approve_discovery(did, user)
        if result["status"] == "success":
            results["approved"] += 1
        else:
            results["errors"] += 1
    return results


def bulk_reject_discoveries(discovery_ids: list[int], user: Any) -> dict[str, Any]:
    """Reject multiple discoveries at once."""
    results = {"rejected": 0, "errors": 0}
    for did in discovery_ids:
        result = reject_discovery(did, user)
        if result["status"] == "success":
            results["rejected"] += 1
        else:
            results["errors"] += 1
    return results


def _update_scan_review_status(scan: Any, user: Any) -> None:
    """Update the parent AutoAdsScanResult status based on child discovery states."""
    from django.utils import timezone

    total = scan.discoveries.count()
    approved = scan.discoveries.filter(status="approved").count()
    rejected = scan.discoveries.filter(status="rejected").count()
    reviewed = approved + rejected

    if reviewed == 0:
        scan.review_status = "pending"
    elif reviewed == total and approved == total:
        scan.review_status = "approved"
    elif reviewed == total and rejected == total:
        scan.review_status = "rejected"
    elif reviewed == total:
        scan.review_status = "partial"
    else:
        scan.review_status = "pending"

    if reviewed > 0:
        scan.reviewed_by = user
        scan.reviewed_at = timezone.now()

    scan.save(update_fields=["review_status", "reviewed_by", "reviewed_at"])


# ===== 3. Blog post body scan (called from Celery task) ===================


def scan_blog_post_body(post_id: int) -> dict[str, Any]:
    """
    Analyse the body HTML of a blog post, count paragraphs, headings, images,
    and create an ``AutoAdsScanResult`` with optimal in-content ad positions.

    Called asynchronously via Celery whenever a blog post is published.
    """
    from apps.ads.models import AutoAdsScanResult

    try:
        from apps.blog.models import Post

        post = Post.objects.filter(pk=post_id).first()
        if post is None:
            return {"status": "error", "reason": "post_not_found"}
    except DatabaseError as exc:
        logger.error("Failed to load post %s: %s", post_id, exc)
        return {"status": "error", "reason": str(exc)}

    body = post.body or ""
    if len(body) < 100:
        return {"status": "skipped", "reason": "body_too_short"}

    # Count structural elements
    paragraphs = len(re.findall(r"<p[\s>]", body, re.IGNORECASE))
    headings = len(re.findall(r"<h[1-6][\s>]", body, re.IGNORECASE))
    images = len(re.findall(r"<img[\s>]", body, re.IGNORECASE))
    lists = len(re.findall(r"<[uo]l[\s>]", body, re.IGNORECASE))
    blockquotes = len(re.findall(r"<blockquote[\s>]", body, re.IGNORECASE))

    content_analysis: dict[str, int] = {}
    if paragraphs:
        content_analysis["paragraphs"] = paragraphs
    if headings:
        content_analysis["headings"] = headings
    if images:
        content_analysis["images"] = images
    if lists:
        content_analysis["lists"] = lists
    if blockquotes:
        content_analysis["blockquotes"] = blockquotes

    if not content_analysis:
        return {"status": "skipped", "reason": "no_structural_content"}

    # Determine in-content ad insertion positions
    suggested: list[dict[str, str]] = []

    if paragraphs >= 3:
        suggested.append(
            {
                "type": "in_article",
                "position": "after_paragraph_3",
                "rationale": "In-article ad after initial engagement (3rd paragraph)",
            }
        )
    if paragraphs >= 6:
        suggested.append(
            {
                "type": "in_article_mid",
                "position": f"after_paragraph_{paragraphs // 2}",
                "rationale": (
                    f"Mid-article ad (paragraph {paragraphs // 2} of {paragraphs})"
                ),
            }
        )
    if paragraphs >= 9:
        suggested.append(
            {
                "type": "in_article_late",
                "position": f"after_paragraph_{paragraphs - 2}",
                "rationale": "Late-article ad for engaged readers",
            }
        )
    if images >= 2:
        suggested.append(
            {
                "type": "after_image",
                "position": "after_second_image",
                "rationale": "Image context ads perform well next to visual content",
            }
        )

    # Score based on content richness
    score = min(
        (
            paragraphs * 5.0
            + headings * 8.0
            + images * 6.0
            + lists * 3.0
            + blockquotes * 2.0
        ),
        100.0,
    )

    template_path = f"blog/post:{post.pk}:{post.slug}"

    # Update-or-create: one scan result per blog post
    AutoAdsScanResult.objects.update_or_create(
        template_path=template_path,
        defaults={
            "content_analysis": content_analysis,
            "suggested_placements": suggested,
            "score": score,
            "applied": False,
            "template_type": "detail",
            "estimated_traffic": "medium",
        },
    )

    logger.info(
        "Blog post scan complete: post=%s, paragraphs=%d, suggestions=%d",
        post_id,
        paragraphs,
        len(suggested),
    )

    return {
        "status": "success",
        "post_id": post_id,
        "paragraphs": paragraphs,
        "suggestions": len(suggested),
        "score": score,
    }


# ===== Helpers =============================================================


def _extract_extends_chain(text: str) -> list[str]:
    """Extract template inheritance chain from extends tags."""
    chain: list[str] = []
    for match in EXTENDS_PATTERN.finditer(text):
        chain.append(match.group("parent"))
    return chain


def _detect_viewport_zones(text: str) -> list[str]:
    """Detect which viewport zones are present in the template."""
    zones: list[str] = []
    for zone_name, pattern in ZONE_PATTERNS.items():
        if pattern.search(text):
            zones.append(zone_name)
    return zones


def _classify_template_type(relative_path: str, text: str) -> str:
    """Auto-classify template type based on name and content."""
    path_lower = relative_path.lower()

    for pattern in DETAIL_PATTERNS:
        if pattern.search(path_lower):
            return "detail"
    for pattern in LIST_PATTERNS:
        if pattern.search(path_lower):
            return "list"

    # Check content for clues
    if re.search(r"<article[^>]*>", text, re.IGNORECASE):
        return "detail"
    if re.search(r"{%\s*for\s+\w+\s+in\s+", text, re.IGNORECASE):
        return "list"
    if re.search(r"<form[^>]*>", text, re.IGNORECASE):
        return "utility"

    # Components / partials
    if "components/" in relative_path:
        return "component"

    return "page"


def _estimate_traffic(relative_path: str, template_type: str) -> str:
    """Estimate traffic level based on template path and type."""
    for pattern in HIGH_TRAFFIC_PATTERNS:
        if pattern.search(relative_path):
            return "high"
    for pattern in LOW_TRAFFIC_PATTERNS:
        if pattern.search(relative_path):
            return "low"

    # Detail & list pages are medium-high; utility is low
    if template_type in ("detail", "list"):
        return "medium"
    if template_type == "utility":
        return "low"

    return "medium"


def _suggest_placements(
    content_analysis: dict[str, int],
    estimated_traffic: str,
) -> list[dict[str, str]]:
    """Generate placement suggestions based on template content analysis."""
    suggestions: list[dict[str, str]] = []

    if "article" in content_analysis or "main_content" in content_analysis:
        suggestions.append(
            {
                "type": "in_article",
                "position": "after_paragraph_3",
                "zone": "mid-content",
                "rationale": (
                    "In-article ads perform well after initial content engagement"
                ),
            }
        )
        suggestions.append(
            {
                "type": "post_top",
                "position": "before_content",
                "zone": "above-fold",
                "rationale": "Above-the-fold placement for high visibility",
            }
        )

    if "sidebar" in content_analysis:
        suggestions.append(
            {
                "type": "sidebar_rect",
                "position": "sidebar_top",
                "zone": "sidebar",
                "rationale": (
                    "Sidebar ads are highly visible without disrupting content"
                ),
            }
        )

    if "list" in content_analysis:
        suggestions.append(
            {
                "type": "in_feed",
                "position": "every_nth_item",
                "zone": "in-feed",
                "rationale": "In-feed ads blend naturally with list content",
            }
        )

    if "footer" in content_analysis:
        suggestions.append(
            {
                "type": "footer_banner",
                "position": "above_footer",
                "zone": "footer",
                "rationale": "Catch users who read to the end",
            }
        )

    # Add traffic context to rationale
    if estimated_traffic == "high":
        for sug in suggestions:
            sug["rationale"] += " (high-traffic template — premium placement)"

    return suggestions


def _calculate_placement_score(
    content_analysis: dict[str, int],
    negative_signals: dict[str, int],
    existing_slots: int,
    estimated_traffic: str,
) -> float:
    """Calculate a score (0–100) indicating how suitable the template is for ads."""
    score = 0.0
    weights = {
        "article": 25.0,
        "main_content": 20.0,
        "sidebar": 15.0,
        "list": 12.0,
        "footer": 8.0,
        "hero": 10.0,
        "card_grid": 10.0,
    }
    for content_type, weight in weights.items():
        if content_type in content_analysis:
            score += weight * min(content_analysis[content_type], 3) / 3.0

    # Negative signal penalties
    penalty = 0.0
    for neg_name, count in negative_signals.items():
        if neg_name == "forms":
            penalty += 10.0 * min(count, 3)
        elif neg_name == "login":
            penalty += 25.0
        elif neg_name == "checkout":
            penalty += 30.0
        elif neg_name == "modal":
            penalty += 5.0 * min(count, 2)

    score = max(score - penalty, 0.0)

    # Ad density penalty (diminishing returns)
    if existing_slots > 0:
        score *= max(0.3, 1.0 - existing_slots * 0.15)

    # Traffic multiplier
    if estimated_traffic == "high":
        score *= 1.2
    elif estimated_traffic == "low":
        score *= 0.7

    return min(score, 100.0)


def _adjust_confidence(
    base_confidence: float,
    estimated_traffic: str,
    negative_signals: dict[str, int],
    ad_density_warning: bool,
) -> float:
    """Adjust per-discovery confidence score based on context."""
    conf = base_confidence

    if estimated_traffic == "high":
        conf = min(conf + 10.0, 100.0)
    elif estimated_traffic == "low":
        conf = max(conf - 15.0, 5.0)

    if negative_signals:
        conf = max(conf - len(negative_signals) * 5.0, 5.0)

    if ad_density_warning:
        conf = max(conf - 20.0, 5.0)

    return round(conf, 2)


def _build_rationale(
    content_type: str,
    zone: str,
    estimated_traffic: str,
    confidence: float,
) -> str:
    """Build a human-readable rationale string for a placement discovery."""
    parts: list[str] = []
    parts.append(f"Detected '{content_type}' content area")
    if zone:
        parts.append(f"in {zone} zone")
    parts.append(f"with {estimated_traffic} estimated traffic")
    parts.append(f"({confidence:.0f}% confidence)")
    return " ".join(parts) + "."
