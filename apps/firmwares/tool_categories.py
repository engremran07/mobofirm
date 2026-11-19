"""Automatic flashing-tool and guide categories.

``FlashingToolCategory`` carries four generated kinds next to the legacy hand-made
``general`` categories:

* one ``chipset_vendor`` category per vendor of the phone/tablet allow-list
  (:data:`apps.firmwares.chipsets.SUPPORTED_CHIPSET_VENDORS`) with its product
  families as ``chipset_family`` children;
* one ``brand`` category per :class:`~apps.firmwares.models.Brand`;
* one ``os_type`` category per device operating system the catalogue supports
  (Android and iOS / iPadOS).

The categories are created and kept in sync here (idempotent; run by the brand and
chipset signals, the seed commands and ``seed_tool_categories``). Which tools and
guides belong to a generated category is *derived* by :class:`CategoryIndex` from the
tool's chipsets, supported brands and legacy category, and the guide's brand and
model chipset, so there is no second stored copy to drift.

Public rule: a category is listed only when it holds at least one active tool or
published guide, or one of its children does (:meth:`CategoryIndex.is_listed`);
empty categories stay in the staff console with their counts and an ``empty`` flag.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from .chipsets import SUPPORTED_CHIPSET_VENDORS, normalize_chipset
from .models import (
    Brand,
    Chipset,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
)
from .package_formats import APPLE_BRAND_SLUGS

logger = logging.getLogger(__name__)

Kind = FlashingToolCategory.Kind

OS_ANDROID = "android"
OS_IOS = "ios"
# (key, name, icon, description). Only systems the catalogue can flash or restore.
OS_TYPES: tuple[tuple[str, str, str, str], ...] = (
    (
        OS_ANDROID,
        "Android",
        "smartphone",
        "Flashing, recovery and unlocking tools and guides for Android phones and tablets.",
    ),
    (
        OS_IOS,
        "iOS / iPadOS",
        "apple",
        (
            "Restore and recovery tools and guides for iPhone and iPad. Only Apple-signed "
            "firmware installs."
        ),
    ),
)


@dataclass(frozen=True)
class VendorSpec:
    """A chipset vendor category and its families (label as stored on ``Chipset``)."""

    vendor: str
    name: str
    families: tuple[tuple[str, str], ...] = ()  # (Chipset.family, category name)


VENDOR_SPECS: tuple[VendorSpec, ...] = (
    VendorSpec("Allwinner", "Allwinner chipsets"),
    VendorSpec(
        "Apple",
        "Apple chipsets",
        (("A-series", "Apple A-series"), ("M-series", "Apple M-series")),
    ),
    VendorSpec("Google", "Google Tensor chipsets", (("Tensor", "Google Tensor"),)),
    VendorSpec(
        "HiSilicon", "HiSilicon Kirin chipsets", (("Kirin", "HiSilicon Kirin"),)
    ),
    VendorSpec(
        "MediaTek",
        "MediaTek chipsets",
        (
            ("Helio", "MediaTek Helio"),
            ("Dimensity", "MediaTek Dimensity"),
            ("Kompanio", "MediaTek Kompanio"),
        ),
    ),
    VendorSpec(
        "Qualcomm", "Qualcomm chipsets", (("Snapdragon", "Qualcomm Snapdragon"),)
    ),
    VendorSpec("Rockchip", "Rockchip chipsets"),
    VendorSpec("Samsung", "Samsung Exynos chipsets", (("Exynos", "Samsung Exynos"),)),
    VendorSpec(
        "Unisoc",
        "Unisoc chipsets",
        (
            ("Unisoc T", "Unisoc T-series"),
            ("Spreadtrum", "Unisoc Spreadtrum"),
            ("RDA", "Unisoc RDA"),
        ),
    ),
)

VENDOR_SORT_BASE = 100
OS_SORT_BASE = 50
BRAND_SORT_BASE = 1000


def vendor_slug(vendor: str) -> str:
    return f"chipsets-{slugify(vendor)}"


def family_slug(vendor: str, family: str) -> str:
    return f"chipsets-{slugify(vendor)}-{slugify(family)}"


def brand_slug(brand: Brand) -> str:
    return f"brand-{brand.slug}"[:120]


def os_slug(key: str) -> str:
    return f"os-{key}"


@dataclass
class SyncReport:
    """What a synchronisation created, updated or left alone."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deactivated: int = 0
    notes: list[str] = field(default_factory=list)

    def record(self, *, created: bool, changed: bool) -> None:
        if created:
            self.created += 1
        elif changed:
            self.updated += 1
        else:
            self.unchanged += 1

    def merge(self, other: SyncReport) -> None:
        self.created += other.created
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.deactivated += other.deactivated
        self.notes += other.notes

    def line(self) -> str:
        return (
            f"{self.created} created, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.deactivated} deactivated"
        )


def _unique_name(name: str, *, exclude_pk: int | None) -> str:
    """``name`` or ``name (2)`` ...: category names are unique across all kinds."""
    candidate, number = name[:100], 2

    def taken(value: str) -> bool:
        rows = FlashingToolCategory.objects.filter(name=value)
        if exclude_pk is not None:
            rows = rows.exclude(pk=exclude_pk)
        return rows.exists()

    while taken(candidate):
        suffix = f" ({number})"
        candidate = f"{name[: 100 - len(suffix)]}{suffix}"
        number += 1
    return candidate


def _apply(
    category: FlashingToolCategory | None,
    *,
    lookup: dict[str, object],
    values: dict[str, object],
    defaults: dict[str, object],
    report: SyncReport,
) -> FlashingToolCategory:
    """Create the generated category, or bring its synchronised fields up to date.

    ``values`` are kept in sync on every run; ``defaults`` (name, description, icon,
    order) only seed a new category so later staff edits are never overwritten.
    """
    if category is None:
        category = FlashingToolCategory(is_auto=True, **lookup)
        for key, value in {**defaults, **values}.items():
            setattr(category, key, value)
        category.name = _unique_name(str(category.name), exclude_pk=None)
        category.save()
        report.record(created=True, changed=False)
        return category
    changed: list[str] = []
    for key, value in {**lookup, **values}.items():
        if key == "name":
            value = _unique_name(str(value), exclude_pk=category.pk)
        if getattr(category, key) != value:
            setattr(category, key, value)
            changed.append(key)
    if not category.is_auto:
        category.is_auto = True
        changed.append("is_auto")
    if changed:
        category.save()
    report.record(created=False, changed=bool(changed))
    return category


def ensure_vendor_categories() -> SyncReport:
    """One category per allow-listed chipset vendor, families as its children."""
    report = SyncReport()
    for order, spec in enumerate(VENDOR_SPECS):
        parent = _apply(
            FlashingToolCategory.objects.filter(
                kind=Kind.CHIPSET_VENDOR, vendor=spec.vendor
            ).first(),
            lookup={"kind": Kind.CHIPSET_VENDOR, "vendor": spec.vendor},
            values={"slug": vendor_slug(spec.vendor)},
            defaults={
                "name": spec.name,
                "icon": "cpu",
                "sort_order": VENDOR_SORT_BASE + order * 10,
                "description": (
                    f"Flashing tools and guides for {spec.vendor} smartphone and "
                    "tablet chipsets."
                ),
            },
            report=report,
        )
        for index, (family, name) in enumerate(spec.families, start=1):
            _apply(
                FlashingToolCategory.objects.filter(
                    kind=Kind.CHIPSET_FAMILY, vendor=spec.vendor, family=family
                ).first(),
                lookup={
                    "kind": Kind.CHIPSET_FAMILY,
                    "vendor": spec.vendor,
                    "family": family,
                },
                values={"slug": family_slug(spec.vendor, family), "parent": parent},
                defaults={
                    "name": name,
                    "icon": "cpu",
                    "sort_order": VENDOR_SORT_BASE + order * 10 + index,
                    "description": f"Tools and guides for {name} chipsets.",
                },
                report=report,
            )
    return report


def ensure_os_categories() -> SyncReport:
    """One category per supported device operating system."""
    report = SyncReport()
    for order, (key, name, icon, description) in enumerate(OS_TYPES):
        _apply(
            FlashingToolCategory.objects.filter(kind=Kind.OS_TYPE, os_key=key).first(),
            lookup={"kind": Kind.OS_TYPE, "os_key": key},
            values={"slug": os_slug(key)},
            defaults={
                "name": name,
                "icon": icon,
                "sort_order": OS_SORT_BASE + order,
                "description": description,
            },
            report=report,
        )
    return report


def ensure_brand_category(brand: Brand, report: SyncReport | None = None) -> None:
    """Create or rename-safely update the category of one brand.

    A category stays attached to its brand when the brand is renamed (the old slug
    is kept in ``slug_history``). A deactivated category left by a deleted brand of
    the same slug is reused.
    """
    report = report if report is not None else SyncReport()
    slug = brand_slug(brand)
    category = FlashingToolCategory.objects.filter(kind=Kind.BRAND, brand=brand).first()
    if category is None:
        category = FlashingToolCategory.objects.filter(
            kind=Kind.BRAND, brand__isnull=True, slug=slug
        ).first()
    history = list(category.slug_history) if category else []
    if category is not None and category.slug != slug and category.slug not in history:
        history.append(category.slug)
    values: dict[str, object] = {
        "name": brand.name,
        "slug": slug,
        "slug_history": history,
    }
    if category is not None and not category.is_active and category.brand_id is None:
        values["is_active"] = True
    try:
        with transaction.atomic():
            _apply(
                category,
                lookup={"kind": Kind.BRAND, "brand": brand},
                values=values,
                defaults={
                    "icon": "smartphone",
                    "sort_order": BRAND_SORT_BASE,
                    "description": f"Flashing tools and guides for {brand.name} devices.",
                },
                report=report,
            )
    except IntegrityError:
        logger.exception("Could not sync the tool category of brand %s", brand.pk)


def ensure_brand_categories() -> SyncReport:
    report = SyncReport()
    for brand in Brand.objects.order_by("name"):
        ensure_brand_category(brand, report)
    return report


def deactivate_brand_category(brand: Brand) -> int:
    """A deleted brand's category is deactivated, never deleted (tools keep working)."""
    return FlashingToolCategory.objects.filter(
        kind=Kind.BRAND, brand=brand, is_active=True
    ).update(is_active=False, updated_at=timezone.now())


def ensure_chipset_categories(chipset: Chipset) -> None:
    """Make sure a chipset's vendor (and family) category exists."""
    if (
        chipset.vendor not in SUPPORTED_CHIPSET_VENDORS
        or FlashingToolCategory.objects.filter(
            kind=Kind.CHIPSET_VENDOR, vendor=chipset.vendor
        ).exists()
    ):
        return
    ensure_vendor_categories()


def sync_reference_categories(*, brands: bool = True) -> SyncReport:
    """Create every missing vendor, family, OS and (optionally) brand category."""
    report = SyncReport()
    with transaction.atomic():
        report.merge(ensure_vendor_categories())
        report.merge(ensure_os_categories())
        if brands:
            report.merge(ensure_brand_categories())
    return report


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def published_guides():
    """Guides whose blog post is live; drafts are never exposed publicly."""
    from apps.blog.models import PostStatus

    return (
        GeneratedFlashingGuide.objects.filter(
            post__status=PostStatus.PUBLISHED, post__publish_at__lte=timezone.now()
        )
        .select_related("post", "brand", "model", "template")
        .order_by("-post__published_at", "-created_at")
    )


@dataclass
class CategoryIndex:
    """Which active tools and published guides belong to which category."""

    categories: list[FlashingToolCategory]
    tools: dict[int, set[int]]
    guides: dict[int, set[int]]
    children: dict[int, list[int]]

    @classmethod
    def build(
        cls, categories: Iterable[FlashingToolCategory] | None = None
    ) -> CategoryIndex:
        cats = (
            list(categories)
            if categories is not None
            else list(FlashingToolCategory.objects.all())
        )
        by_vendor: dict[str, int] = {}
        by_family: dict[tuple[str, str], int] = {}
        by_brand: dict[int, int] = {}
        by_os: dict[str, int] = {}
        for cat in cats:
            if cat.kind == Kind.CHIPSET_VENDOR:
                by_vendor[cat.vendor] = cat.pk
            elif cat.kind == Kind.CHIPSET_FAMILY:
                by_family[(cat.vendor, cat.family)] = cat.pk
            elif cat.kind == Kind.BRAND and cat.brand_id:
                by_brand[cat.brand_id] = cat.pk
            elif cat.kind == Kind.OS_TYPE:
                by_os[cat.os_key] = cat.pk
        tools: dict[int, set[int]] = defaultdict(set)
        guides: dict[int, set[int]] = defaultdict(set)
        apple_brand_ids = set(
            Brand.objects.filter(slug__in=APPLE_BRAND_SLUGS).values_list(
                "pk", flat=True
            )
        )
        cls._index_tools(tools, by_vendor, by_family, by_brand, by_os, apple_brand_ids)
        cls._index_guides(guides, by_vendor, by_family, by_brand, by_os)
        children: dict[int, list[int]] = defaultdict(list)
        for cat in cats:
            if cat.parent_id:
                children[cat.parent_id].append(cat.pk)
        return cls(cats, tools, guides, children)

    @staticmethod
    def _index_tools(
        tools: dict[int, set[int]],
        by_vendor: dict[str, int],
        by_family: dict[tuple[str, str], int],
        by_brand: dict[int, int],
        by_os: dict[str, int],
        apple_brand_ids: set[int],
    ) -> None:
        active = dict(
            FlashingTool.objects.filter(is_active=True).values_list("pk", "category_id")
        )
        for tool_id, legacy in active.items():
            tools[legacy].add(tool_id)
        brand_rows = FlashingTool.supported_brands.through.objects.filter(
            flashingtool_id__in=active
        ).values_list("flashingtool_id", "brand_id")
        chipset_rows = (
            FlashingTool.chipsets.through.objects.filter(flashingtool_id__in=active)
            .values_list("flashingtool_id", "chipset__vendor", "chipset__family")
            .distinct()
        )
        ios: set[int] = set()
        for tool_id, brand_id in brand_rows:
            if brand_id in by_brand:
                tools[by_brand[brand_id]].add(tool_id)
            if brand_id in apple_brand_ids:
                ios.add(tool_id)
        for tool_id, vendor, family in chipset_rows:
            if vendor in by_vendor:
                tools[by_vendor[vendor]].add(tool_id)
            if (vendor, family) in by_family:
                tools[by_family[(vendor, family)]].add(tool_id)
            if vendor == "Apple":
                ios.add(tool_id)
        for tool_id in active:
            key = OS_IOS if tool_id in ios else OS_ANDROID
            if key in by_os:
                tools[by_os[key]].add(tool_id)

    @staticmethod
    def _index_guides(
        guides: dict[int, set[int]],
        by_vendor: dict[str, int],
        by_family: dict[tuple[str, str], int],
        by_brand: dict[int, int],
        by_os: dict[str, int],
    ) -> None:
        rows = published_guides().values_list(
            "pk",
            "brand_id",
            "brand__slug",
            "model__chipset_ref__vendor",
            "model__chipset_ref__family",
            "chipset",
        )
        for guide_id, brand_id, brand_slug_, vendor, family, text in rows:
            if not vendor:
                normalized = normalize_chipset(text)
                if normalized is not None:
                    vendor, family = normalized.vendor, normalized.family
            apple = brand_slug_ in APPLE_BRAND_SLUGS
            if apple and not vendor:
                vendor = "Apple"
            if brand_id in by_brand:
                guides[by_brand[brand_id]].add(guide_id)
            if vendor in by_vendor:
                guides[by_vendor[vendor]].add(guide_id)
            if (vendor, family) in by_family:
                guides[by_family[(vendor, family)]].add(guide_id)
            key = OS_IOS if apple else OS_ANDROID
            if key in by_os:
                guides[by_os[key]].add(guide_id)

    # -- queries ----------------------------------------------------------

    def tool_ids(self, category: FlashingToolCategory) -> set[int]:
        return self.tools.get(category.pk, set())

    def guide_ids(self, category: FlashingToolCategory) -> set[int]:
        return self.guides.get(category.pk, set())

    def counts(self, category: FlashingToolCategory) -> tuple[int, int]:
        return len(self.tool_ids(category)), len(self.guide_ids(category))

    def is_empty(self, category: FlashingToolCategory) -> bool:
        return self.counts(category) == (0, 0)

    def is_listed(self, category: FlashingToolCategory) -> bool:
        """Public rule: active and holding content, or a child that does."""
        if not category.is_active:
            return False
        if not self.is_empty(category):
            return True
        by_pk = {cat.pk: cat for cat in self.categories}
        return any(
            self.is_listed(by_pk[child])
            for child in self.children.get(category.pk, ())
            if child in by_pk
        )

    def listed(self) -> list[FlashingToolCategory]:
        return [cat for cat in self.categories if self.is_listed(cat)]

    def listed_children(
        self, category: FlashingToolCategory
    ) -> list[FlashingToolCategory]:
        return [
            cat
            for cat in self.categories
            if cat.parent_id == category.pk and self.is_listed(cat)
        ]


# Order of the public "browse by" groups.
PUBLIC_GROUPS: tuple[tuple[str, str], ...] = (
    (Kind.CHIPSET_VENDOR, "Chipset"),
    (Kind.BRAND, "Brand"),
    (Kind.OS_TYPE, "Operating system"),
)


def listed_groups(
    index: CategoryIndex,
) -> list[tuple[str, list[tuple[FlashingToolCategory, list[FlashingToolCategory]]]]]:
    """``[(label, [(category, listed children)])]`` for the public browse-by nav."""
    listed = index.listed()
    groups = []
    for kind, label in PUBLIC_GROUPS:
        rows = [(cat, index.listed_children(cat)) for cat in listed if cat.kind == kind]
        if rows:
            groups.append((label, rows))
    return groups


def resolve_category(slug: str) -> tuple[FlashingToolCategory | None, bool]:
    """``(category, was_renamed)`` for a current or earlier slug."""
    category = FlashingToolCategory.objects.filter(slug=slug).first()
    if category is not None:
        return category, False
    for candidate in FlashingToolCategory.objects.filter(is_auto=True, kind=Kind.BRAND):
        if slug in candidate.slug_history:
            return candidate, True
    return None, False
