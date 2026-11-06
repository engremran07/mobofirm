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

# (vendor label, pattern identifying the vendor in raw text)
_VENDOR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MediaTek", re.compile(r"mediatek|\bmt\s?\d{4}|helio|dimensity", re.IGNORECASE)),
    (
        "Qualcomm",
        re.compile(r"qualcomm|snapdragon|\bsm\d{4}|\bsdm\d+|\bmsm\d+", re.IGNORECASE),
    ),
    ("Samsung", re.compile(r"exynos", re.IGNORECASE)),
    ("Unisoc", re.compile(r"unisoc|spreadtrum|\bsc\d{4}", re.IGNORECASE)),
    ("Apple", re.compile(r"\bapple\b", re.IGNORECASE)),
    ("HiSilicon", re.compile(r"hisilicon|kirin", re.IGNORECASE)),
    ("Google", re.compile(r"\bgoogle\b|\btensor\b", re.IGNORECASE)),
    ("Rockchip", re.compile(r"rockchip|\brk\d{4}", re.IGNORECASE)),
    ("Intel", re.compile(r"\bintel\b|\batom\b", re.IGNORECASE)),
)
_VENDOR_PREFIX = re.compile(
    r"^(mediatek|qualcomm|samsung|unisoc|spreadtrum|apple|hisilicon|google|rockchip|intel)\s+",
    re.IGNORECASE,
)
_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Helio", re.compile(r"\bhelio\b", re.IGNORECASE)),
    ("Dimensity", re.compile(r"\bdimensity\b", re.IGNORECASE)),
    ("Snapdragon", re.compile(r"\bsnapdragon\b", re.IGNORECASE)),
    ("Exynos", re.compile(r"\bexynos\b", re.IGNORECASE)),
    ("Kirin", re.compile(r"\bkirin\b", re.IGNORECASE)),
    ("Tensor", re.compile(r"\btensor\b", re.IGNORECASE)),
    ("Unisoc T", re.compile(r"^t\d{3}\b", re.IGNORECASE)),
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
    name = _VENDOR_PREFIX.sub("", cleaned).strip()
    if not vendor:
        parts = cleaned.split(" ", 1)
        if len(parts) < 2:
            return None
        vendor, name = parts[0].title(), parts[1]
    if not name:
        name = cleaned
    family = ""
    for label, pattern in _FAMILY_PATTERNS:
        if pattern.search(name):
            family = label
            break
    slug = slugify(f"{vendor} {name}")[:160]
    if not slug:
        return None
    return NormalizedChipset(vendor=vendor, family=family, name=name[:128], slug=slug)


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
