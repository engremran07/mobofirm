"""Placement manager: which scanned templates can carry ads, and putting ads on them.

Placing an ad never edits template source. Pages declare *zones* with
``{% ad_zone "<code>" %}`` (see ``apps.ads.zones``); this module reads the templates to find
which zones each page (and the layouts it extends) declares, and creates or updates the
``AdPlacement`` data that decides what fills a zone on which template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.template.defaultfilters import slugify

from apps.ads.models import (
    AdCreative,
    AdPlacement,
    AutoAdsScanResult,
    TemplateAdExclusion,
)
from apps.ads.zones import (
    DEVICE_CHOICES,
    SIZE_PATTERN,
    ZONE_BY_CODE,
    Zone,
    zone_for_suggestion,
)

ZONE_TAG = re.compile(r"""\{%\s*ad_zone\s+["'](?P<zone>[a-z_]+)["']""")
EXTENDS_TAG = re.compile(r"""\{%\s*extends\s+["'](?P<name>[^"']+)["']""")
INCLUDE_TAG = re.compile(r"""\{%\s*include\s+["'](?P<name>[^"']+)["']""")
MAX_DEPTH = 6
LAYOUT = "base.html"


class PlacementError(ValueError):
    """A placement request the vocabulary or the data rules reject."""


def templates_root() -> Path:
    return Path(settings.BASE_DIR) / "templates"


def _read(name: str) -> str:
    root = templates_root().resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def declared_zones(
    template_path: str, _cache: dict[str, frozenset[str]] | None = None
) -> frozenset[str]:
    """Zone codes ``template_path`` renders: its own tags plus extended/included ones."""
    cache = _cache if _cache is not None else {}
    return _declared(template_path, cache, 0)


def _declared(
    name: str, cache: dict[str, frozenset[str]], depth: int
) -> frozenset[str]:
    if name in cache:
        return cache[name]
    if depth > MAX_DEPTH:
        return frozenset()
    cache[name] = frozenset()  # breaks include cycles
    source = _read(name)
    found = {m.group("zone") for m in ZONE_TAG.finditer(source)}
    for pattern in (EXTENDS_TAG, INCLUDE_TAG):
        for match in pattern.finditer(source):
            found |= _declared(match.group("name"), cache, depth + 1)
    result = frozenset(code for code in found if code in ZONE_BY_CODE)
    cache[name] = result
    return result


def site_wide_zones() -> frozenset[str]:
    """Zones the shared layout declares, so a placement may fill them on every page."""
    return declared_zones(LAYOUT)


def site_wide_rows() -> list[tuple[Zone, AdPlacement | None]]:
    """Each shared-layout zone with the site-wide placement filling it, if any."""
    declared = site_wide_zones()
    placements = {
        p.zone: p
        for p in AdPlacement.objects.filter(template_reference="", is_deleted=False)
        .exclude(zone="")
        .order_by("-pk")
    }
    return [
        (zone, placements.get(zone.code))
        for zone in ZONE_BY_CODE.values()
        if zone.code in declared
    ]


@dataclass(slots=True)
class TemplateRow:
    path: str
    template_type: str
    type_label: str
    traffic: str
    score: float
    rationale: str
    review_status: str
    excluded: bool
    declared: list[Zone]
    placements: list[AdPlacement] = field(default_factory=list)
    suggestions: list[dict[str, object]] = field(default_factory=list)
    pending: int = 0
    preview: str = ""

    @property
    def free_zones(self) -> list[Zone]:
        used = {p.zone for p in self.placements}
        return [zone for zone in self.declared if zone.code not in used]


def template_rows(
    *,
    search: str = "",
    template_type: str = "",
    state: str = "",
) -> list[TemplateRow]:
    """One row per scanned template, best candidates first.

    ``state``: ``placed`` (has a placement), ``open`` (declares a free zone) or
    ``blocked`` (excluded from ads).
    """
    excluded = set(TemplateAdExclusion.objects.values_list("template_path", flat=True))
    placements: dict[str, list[AdPlacement]] = {}
    for placement in AdPlacement.objects.filter(is_deleted=False).exclude(
        template_reference=""
    ):
        placements.setdefault(placement.template_reference, []).append(placement)
    cache: dict[str, frozenset[str]] = {}
    scans = AutoAdsScanResult.objects.prefetch_related("discoveries")
    if search:
        scans = scans.filter(template_path__icontains=search)
    if template_type:
        scans = scans.filter(template_type=template_type)
    rows: list[TemplateRow] = []
    seen: set[str] = set()
    for scan in scans.order_by("template_path", "-created_at"):
        if scan.template_path in seen:
            continue
        seen.add(scan.template_path)
        zones = [
            ZONE_BY_CODE[code]
            for code in sorted(
                declared_zones(scan.template_path, cache),
                key=lambda c: list(ZONE_BY_CODE).index(c),
            )
        ]
        row = TemplateRow(
            path=scan.template_path,
            template_type=scan.template_type,
            type_label=scan.get_template_type_display(),
            traffic=scan.estimated_traffic,
            score=float(scan.score),
            rationale=scan.ai_recommendation or _first_rationale(scan),
            review_status=scan.review_status,
            excluded=scan.template_path in excluded,
            declared=zones,
            placements=sorted(
                placements.get(scan.template_path, []), key=lambda p: p.zone
            ),
            suggestions=_suggestions(scan),
            pending=sum(1 for d in scan.discoveries.all() if d.status == "pending"),
        )
        if state == "placed" and not row.placements:
            continue
        if state == "open" and not (row.free_zones and not row.excluded):
            continue
        if state == "blocked" and not row.excluded:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (-bool(r.declared), -r.score, r.path))
    return rows


def _suggestions(scan: AutoAdsScanResult) -> list[dict[str, object]]:
    """Scanner discoveries of the template with the layout zone each would fill."""
    return [
        {
            "code": discovery.placement_code,
            "zone": zone_for_suggestion(discovery.placement_code),
            "confidence": float(discovery.confidence),
            "status": discovery.status,
            "rationale": discovery.rationale,
        }
        for discovery in sorted(
            scan.discoveries.all(), key=lambda d: -float(d.confidence)
        )
    ]


def _first_rationale(scan: AutoAdsScanResult) -> str:
    for discovery in scan.discoveries.all():
        if discovery.rationale:
            return discovery.rationale
    return ""


def _unique(model_field: str, base: str, limit: int) -> str:
    value = base[:limit]
    suffix = 1
    while AdPlacement.objects.filter(**{model_field: value}).exists():
        suffix += 1
        tail = f"-{suffix}"
        value = f"{base[: limit - len(tail)]}{tail}"
    return value


def clean_sizes(raw: str, zone: Zone) -> str:
    """Normalised comma list of ``WxH`` sizes; the zone's default when empty."""
    sizes = [s.strip().lower() for s in (raw or "").split(",") if s.strip()]
    if not sizes:
        return zone.default_size
    for size in sizes:
        if not SIZE_PATTERN.match(size):
            raise PlacementError(f"“{size}” is not a size like 300x250.")
    return ",".join(dict.fromkeys(sizes))


@transaction.atomic
def place_ad(
    *,
    user: object,
    template_path: str,
    zone_code: str,
    sizes: str = "",
    device: str = "all",
    fallback: AdCreative | None = None,
    existing: AdPlacement | None = None,
    enabled: bool = False,
) -> AdPlacement:
    """Create (or link an existing code-addressed placement as) the placement filling
    ``zone_code`` on ``template_path``. An empty template path means site-wide.

    New placements start switched off (draft) unless ``enabled``; they serve nothing
    until a creative is assigned and the placement is enabled.
    """
    zone = ZONE_BY_CODE.get(zone_code)
    actor_id = getattr(user, "pk", None)
    if zone is None:
        raise PlacementError("Choose one of the ad zones.")
    if device not in {code for code, _ in DEVICE_CHOICES}:
        raise PlacementError("Choose a device target.")
    template_path = template_path.strip()
    allowed = declared_zones(template_path) if template_path else site_wide_zones()
    if zone_code not in allowed:
        scope = template_path or "the shared layout"
        raise PlacementError(
            f"{scope} does not declare the “{zone.label}” zone, so an ad there would "
            "never show."
        )
    if (
        template_path
        and TemplateAdExclusion.objects.filter(template_path=template_path).exists()
    ):
        raise PlacementError("This template is excluded from ads.")
    clash = AdPlacement.objects.filter(
        template_reference=template_path, zone=zone_code, is_deleted=False
    )
    if existing is not None:
        clash = clash.exclude(pk=existing.pk)
    if clash.exists():
        raise PlacementError("This zone already has a placement on that template.")
    size_list = clean_sizes(sizes, zone)
    if existing is not None:
        placement = existing
    else:
        label = template_path or "Site-wide"
        base = f"{label} · {zone.label}"
        placement = AdPlacement(
            name=_unique("name", base, 150),
            code=_unique("code", slugify(f"{label}-{zone.code}"), 120),
            slug=_unique("slug", slugify(f"{label}-{zone.code}"), 180),
            description=f"{zone.description} ({label})",
            is_enabled=enabled,
            created_by_id=actor_id,
        )
    placement.template_reference = template_path
    placement.zone = zone_code
    placement.device_target = device
    placement.allowed_sizes = size_list
    placement.fallback_creative = fallback
    placement.context = (
        template_path.split("/")[0] if "/" in template_path else "global"
    )
    placement.page_context = template_path
    if existing is None or enabled:
        placement.is_enabled = enabled
    placement.updated_by_id = actor_id
    placement.save()
    return placement
