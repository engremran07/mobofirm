"""Search-as-you-type pickers for seller specialities (brands and chipsets)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from django.db.models import Q
from django.urls import reverse

from apps.firmwares.models import Brand, Chipset

from .models import MAX_SPECIALITIES

RESULT_LIMIT: Final[int] = 12
MAX_QUERY_LENGTH: Final[int] = 60


@dataclass(frozen=True)
class PickerOption:
    pk: int
    label: str
    hint: str = ""


def _brand_option(brand: Brand) -> PickerOption:
    return PickerOption(brand.pk, brand.name)


def _chipset_option(chipset: Chipset) -> PickerOption:
    return PickerOption(chipset.pk, chipset.display_name, chipset.family)


PICKER_LABELS: Final[dict[str, tuple[str, str]]] = {
    "brands": ("Brand specialities", "brand"),
    "chipsets": ("Chipset specialities", "chipset"),
}


def is_picker_kind(kind: str) -> bool:
    return kind in PICKER_LABELS


def clean_ids(raw: list[str]) -> list[int]:
    ids: list[int] = []
    for value in raw[:MAX_SPECIALITIES]:
        if value.isdecimal() and len(value) < 10:
            ids.append(int(value))
    return ids


def selected_options(kind: str, ids: list[int]) -> list[PickerOption]:
    if kind == "brands":
        return [_brand_option(b) for b in Brand.objects.filter(pk__in=ids)]
    return [_chipset_option(c) for c in Chipset.objects.filter(pk__in=ids)]


def search_options(kind: str, query: str, exclude: list[int]) -> list[PickerOption]:
    query = query.strip()[:MAX_QUERY_LENGTH]
    if kind == "brands":
        brands = Brand.objects.exclude(pk__in=exclude)
        if query:
            brands = brands.filter(name__icontains=query)
        else:
            brands = brands.order_by("-is_featured", "name")
        return [_brand_option(b) for b in brands[:RESULT_LIMIT]]
    chipsets = Chipset.objects.exclude(pk__in=exclude)
    if query:
        chipsets = chipsets.filter(
            Q(name__icontains=query)
            | Q(vendor__icontains=query)
            | Q(family__icontains=query)
        )
    return [_chipset_option(c) for c in chipsets[:RESULT_LIMIT]]


def picker_context(
    kind: str, selected_ids: list[int], query: str = ""
) -> dict[str, Any]:
    """Template context for the picker fieldset and its HTMX body."""
    label, noun = PICKER_LABELS[kind]
    return {
        "kind": kind,
        "label": label,
        "noun": noun,
        "url": reverse("marketplace:picker", kwargs={"kind": kind}),
        "query": query,
        "selected": selected_options(kind, selected_ids),
        "results": search_options(kind, query, selected_ids),
        "limit": MAX_SPECIALITIES,
    }
