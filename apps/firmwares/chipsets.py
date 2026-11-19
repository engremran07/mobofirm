"""Chipset normalisation and resolution.

Raw chipset text (``"Mediatek Helio G100 Ultra (6nm)"``) is reduced to a vendor,
family and name, then resolved to a single :class:`~apps.firmwares.models.Chipset`
row so models, variants, tools and pages share one entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.utils.text import slugify

MAX_RAW_LENGTH = 256

MEDIATEK = "MediaTek"
QUALCOMM = "Qualcomm"
SAMSUNG = "Samsung"
UNISOC = "Unisoc"
HISILICON = "HiSilicon"
GOOGLE = "Google"
ROCKCHIP = "Rockchip"
ALLWINNER = "Allwinner"
APPLE = "Apple"

# Smartphone and tablet SoC vendors only (canonical labels). Samsung means Exynos,
# HiSilicon means Kirin, Google means Tensor, Apple means the A-series (iPhone, iPad)
# and M-series (iPad) chips, and Unisoc also covers Spreadtrum and the RDA
# Microelectronics parts Unisoc acquired. Nothing outside this tuple (for example
# Intel or Mac silicon naming) is ever resolved to a Chipset row.
SUPPORTED_CHIPSET_VENDORS: tuple[str, ...] = (
    MEDIATEK,
    QUALCOMM,
    SAMSUNG,
    UNISOC,
    HISILICON,
    GOOGLE,
    ROCKCHIP,
    ALLWINNER,
    APPLE,
)

# Lower-cased spellings that map to a canonical vendor label.
_VENDOR_ALIASES: dict[str, str] = {
    "mediatek": MEDIATEK,
    "mtk": MEDIATEK,
    "qualcomm": QUALCOMM,
    "snapdragon": QUALCOMM,
    "samsung": SAMSUNG,
    "samsung exynos": SAMSUNG,
    "exynos": SAMSUNG,
    "unisoc": UNISOC,
    "spreadtrum": UNISOC,
    "sprd": UNISOC,
    "rda": UNISOC,
    "rda microelectronics": UNISOC,
    "hisilicon": HISILICON,
    "huawei hisilicon": HISILICON,
    "kirin": HISILICON,
    "google": GOOGLE,
    "google tensor": GOOGLE,
    "tensor": GOOGLE,
    "rockchip": ROCKCHIP,
    "allwinner": ALLWINNER,
    "apple": APPLE,
    "apple silicon": APPLE,
}

# (vendor label, pattern identifying the vendor in raw text)
_VENDOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (MEDIATEK, re.compile(r"mediatek|\bmt\s?\d{4}|helio|dimensity", re.IGNORECASE)),
    (
        QUALCOMM,
        re.compile(
            r"qualcomm|snapdragon|\bsm\d{4}|\bsdm\d+|\bmsm\d+|\bqcm\d{4}",
            re.IGNORECASE,
        ),
    ),
    (SAMSUNG, re.compile(r"exynos", re.IGNORECASE)),
    (
        UNISOC,
        re.compile(
            r"unisoc|spreadtrum|\bsprd\b|\bsc\d{4}|\bums\d{3,4}"
            r"|rda microelectronics|\brda\s?\d{4}",
            re.IGNORECASE,
        ),
    ),
    (APPLE, re.compile(r"\bapple\b|\bbionic\b", re.IGNORECASE)),
    (HISILICON, re.compile(r"hisilicon|kirin", re.IGNORECASE)),
    (GOOGLE, re.compile(r"\bgoogle\b|\btensor\b", re.IGNORECASE)),
    (ROCKCHIP, re.compile(r"rockchip|\brk\d{4}", re.IGNORECASE)),
    (ALLWINNER, re.compile(r"allwinner|sunxi|\bsun\d+i\b", re.IGNORECASE)),
)
_VENDOR_PREFIX = re.compile(
    r"^(mediatek|qualcomm|samsung|unisoc|spreadtrum|sprd|apple|hisilicon|google"
    r"|rockchip|allwinner|rda microelectronics)\s+",
    re.IGNORECASE,
)
# (vendor, family label, pattern on the name). Families are scoped to their vendor
# so an Allwinner T507 is never mistaken for a Unisoc T-series part.
_FAMILY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (MEDIATEK, "Helio", re.compile(r"\bhelio\b", re.IGNORECASE)),
    (MEDIATEK, "Dimensity", re.compile(r"\bdimensity\b", re.IGNORECASE)),
    (QUALCOMM, "Snapdragon", re.compile(r"\bsnapdragon\b", re.IGNORECASE)),
    (SAMSUNG, "Exynos", re.compile(r"\bexynos\b", re.IGNORECASE)),
    (HISILICON, "Kirin", re.compile(r"\bkirin\b", re.IGNORECASE)),
    (GOOGLE, "Tensor", re.compile(r"\btensor\b", re.IGNORECASE)),
    (UNISOC, "Unisoc T", re.compile(r"^t\d{3}\b", re.IGNORECASE)),
    (UNISOC, "RDA", re.compile(r"^rda\s?\d{4}", re.IGNORECASE)),
    (ALLWINNER, "Allwinner A", re.compile(r"^a\d{2,3}[a-z]?\b", re.IGNORECASE)),
    (ALLWINNER, "Allwinner T", re.compile(r"^t\d{3}\b", re.IGNORECASE)),
    (APPLE, "A-series", re.compile(r"^a\d{2}\b", re.IGNORECASE)),
    (APPLE, "M-series", re.compile(r"^m\d\b", re.IGNORECASE)),
)
_NOISE = re.compile(r"\([^)]*\)|\bprocessor\b|\bchipset\b|\bsoc\b", re.IGNORECASE)


@dataclass(frozen=True)
class NormalizedChipset:
    vendor: str
    family: str
    name: str
    slug: str

    @property
    def display_name(self) -> str:
        return f"{self.vendor} {self.name}".strip()


def normalize_chipset(raw: str | None) -> NormalizedChipset | None:
    """Return the normalised vendor/family/name for raw chipset text."""
    text = re.sub(r"\s+", " ", (raw or "").strip())[:MAX_RAW_LENGTH]
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", _NOISE.sub(" ", text)).strip(" -,/")
    if not cleaned:
        cleaned = text
    vendor = ""
    for label, pattern in _VENDOR_PATTERNS:
        if pattern.search(cleaned):
            vendor = label
            break
    if not vendor:
        # Not a smartphone/tablet SoC vendor we support (e.g. Intel, Apple silicon).
        return None
    name = _VENDOR_PREFIX.sub("", cleaned).strip()
    if not name:
        name = cleaned
    family = ""
    for family_vendor, label, pattern in _FAMILY_PATTERNS:
        if family_vendor == vendor and pattern.search(name):
            family = label
            break
    slug = slugify(f"{vendor} {name}")[:160]
    if not slug:
        return None
    return NormalizedChipset(vendor=vendor, family=family, name=name[:128], slug=slug)


def canonical_vendor(label: str | None) -> str:
    """The supported vendor label for a stored vendor/chipset label, or ``""``.

    Accepts the canonical labels, aliases such as ``Spreadtrum`` or ``RDA`` (a
    Unisoc-owned label) and falls back to pattern matching on the text.
    """
    text = re.sub(r"\s+", " ", (label or "").strip()).lower()
    if not text:
        return ""
    alias = _VENDOR_ALIASES.get(text)
    if alias:
        return alias
    for canonical in SUPPORTED_CHIPSET_VENDORS:
        if canonical.lower() == text:
            return canonical
    return vendor_for(label)


def is_supported_vendor(label: str | None) -> bool:
    """True when the vendor label belongs to the phone/tablet SoC allow-list."""
    return bool(canonical_vendor(label))


def vendor_for(raw: str | None) -> str:
    """Vendor label used for tool compatibility matching (empty when unknown)."""
    normalized = normalize_chipset(raw)
    return normalized.vendor if normalized else ""


def _remember_alias(chipset: Any, raw: str) -> None:
    alias = re.sub(r"\s+", " ", raw.strip())[:MAX_RAW_LENGTH]
    known = {chipset.display_name.lower(), chipset.name.lower()} | {
        str(item).lower() for item in chipset.aliases
    }
    if alias and alias.lower() not in known:
        chipset.aliases = [*chipset.aliases, alias]
        chipset.save(update_fields=["aliases", "updated_at"])


def resolve_chipset(raw: str | None, *, create: bool = True):
    """Find (or create) the Chipset row for raw chipset text."""
    from .models import Chipset

    normalized = normalize_chipset(raw)
    if normalized is None:
        return None
    raw_text = (raw or "").strip()
    chipset = Chipset.objects.filter(slug=normalized.slug).first()
    if chipset is None:
        lowered = raw_text.lower()
        for candidate in Chipset.objects.filter(vendor=normalized.vendor):
            if lowered in {str(alias).lower() for alias in candidate.aliases}:
                chipset = candidate
                break
    if chipset is None:
        if not create:
            return None
        try:
            with transaction.atomic():
                chipset = Chipset.objects.create(
                    vendor=normalized.vendor,
                    family=normalized.family,
                    name=normalized.name,
                    slug=normalized.slug,
                )
        except IntegrityError:
            chipset = Chipset.objects.get(slug=normalized.slug)
    _remember_alias(chipset, raw_text)
    return chipset


def sync_chipset_reference(
    instance: Any, save_kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Keep ``chipset_ref`` aligned with ``chipset`` text before a save.

    Returns the save kwargs, widening ``update_fields`` when the reference changes
    so partial saves persist the resolved chipset.
    """
    update_fields = save_kwargs.get("update_fields")
    if update_fields is not None and not {"chipset", "chipset_ref"} & set(
        update_fields
    ):
        return save_kwargs
    if instance.chipset:
        resolved = resolve_chipset(instance.chipset)
        if resolved is not None and instance.chipset_ref_id != resolved.pk:
            instance.chipset_ref = resolved
        elif resolved is None:
            instance.chipset_ref = None
    elif instance.chipset_ref_id:
        max_length = type(instance)._meta.get_field("chipset").max_length
        instance.chipset = instance.chipset_ref.display_name[:max_length]
    if update_fields is not None:
        save_kwargs = {
            **save_kwargs,
            "update_fields": [*update_fields, "chipset", "chipset_ref"],
        }
    return save_kwargs
