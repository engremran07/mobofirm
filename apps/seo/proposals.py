"""Standards-based fix proposals built from an object's own stored data.

A proposal never invents facts: titles and descriptions are composed from fields that
exist on the content object (name, summary, codes, chipset, counts). When the stored
data is too thin to reach the standard, no proposal is returned and the issue stays
for an editor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.apps import apps as django_apps
from django.db import models

from .managers import default_manager
from .models import SEOSettings
from .policy import clean_text
from .rules import DESCRIPTION_MAX, DESCRIPTION_MIN, TITLE_MAX, TITLE_MIN

TITLE_FIELD = "meta_title"
DESCRIPTION_FIELD = "meta_description"
GENERIC_SOURCES = frozenset({"default", "fallback"})


@dataclass(frozen=True)
class Proposal:
    field: str
    value: str
    basis: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "value": self.value, "basis": self.basis}


def load_object(label: str, pk: str) -> models.Model | None:
    try:
        model = django_apps.get_model(label)
    except (LookupError, ValueError):
        return None
    return default_manager(model).filter(pk=pk).first()


def _word_cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-–—|")


def fit_title(base: str, site_name: str, separator: str) -> str:
    """Title within the length standard, keeping the site name when it fits."""
    base = clean_text(base)
    if not base:
        return ""
    full = f"{base}{separator}{site_name}" if site_name else base
    if len(full) <= TITLE_MAX:
        return full
    return _word_cut(base, TITLE_MAX)


def fit_description(text: str) -> str:
    """Description within the length standard, cut at a sentence or word boundary."""
    text = clean_text(text)
    if len(text) <= DESCRIPTION_MAX:
        return text
    window = text[:DESCRIPTION_MAX]
    stop = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if stop >= DESCRIPTION_MIN:
        return window[: stop + 1]
    return _word_cut(text, DESCRIPTION_MAX - 1).rstrip(".") + "."


def _first(*values: Any) -> str:
    for value in values:
        text = clean_text(str(value or ""))
        if text:
            return text
    return ""


def _model_name(obj: Any) -> str:
    brand = getattr(obj, "brand", None)
    name = _first(getattr(obj, "display_name", ""), getattr(obj, "name", ""))
    if brand is not None and brand.name.lower() not in name.lower():
        return f"{brand.name} {name}"
    return name


def _base_title(label: str, obj: Any) -> str:
    if label == "firmwares.model":
        return f"{_model_name(obj)} firmware download"
    if label == "firmwares.brand":
        return f"{obj.name} firmware downloads"
    if label == "firmwares.chipset":
        return f"{obj.display_name} firmware and flashing tools"
    if label == "firmwares.flashingtool":
        return f"{obj.name} flashing tool"
    return _first(
        getattr(obj, "seo_title", ""),
        getattr(obj, "title", ""),
        getattr(obj, "name", ""),
        getattr(obj, "slug", ""),
    )


def propose_title(
    label: str, obj: Any, settings_row: SEOSettings, site_name: str
) -> Proposal | None:
    base = _base_title(label, obj)
    value = fit_title(base, site_name, settings_row.title_separator)
    if len(value) < TITLE_MIN:
        return None
    return Proposal(TITLE_FIELD, value, f"{label} name and page type")


def _model_description(obj: Any) -> str:
    facts = [
        f"model code {obj.all_model_codes_display}"
        if getattr(obj, "all_model_codes_display", "")
        else "",
        f"chipset {obj.chipset}" if getattr(obj, "chipset", "") else "",
        f"launch OS {obj.os_version}" if getattr(obj, "os_version", "") else "",
    ]
    detail = ", ".join(fact for fact in facts if fact)
    head = (
        f"Firmware downloads, specifications and flashing tools for {_model_name(obj)}"
    )
    return f"{head} ({detail})." if detail else ""


def _brand_description(obj: Any) -> str:
    stored = _first(obj.description)
    models_total = obj.models.filter(is_active=True).count()
    head = f"{obj.name} firmware downloads and {models_total} device models"
    return f"{head}. {stored}" if stored else f"{head} with flashing tools and guides."


def _chipset_description(obj: Any) -> str:
    models_total = obj.models.filter(is_active=True).count()
    tools_total = obj.flashing_tools.filter(is_active=True).count()
    return (
        f"{obj.display_name} chipset: firmware, {tools_total} supported flashing "
        f"tools and {models_total} device models."
    )


def _tool_description(obj: Any) -> str:
    stored = _first(obj.description)
    if stored:
        return stored
    return f"{obj.name}, a {obj.get_platform_display()} tool for flashing firmware."


def propose_description(label: str, obj: Any) -> Proposal | None:
    candidates: list[tuple[str, str]] = []
    if label == "firmwares.model":
        candidates.append((_first(obj.description), "model description"))
        candidates.append((_model_description(obj), "model facts"))
    elif label == "firmwares.brand":
        candidates.append((_brand_description(obj), "brand description and counts"))
    elif label == "firmwares.chipset":
        candidates.append((_chipset_description(obj), "chipset counts"))
    elif label == "firmwares.flashingtool":
        candidates.append((_tool_description(obj), "tool description"))
    else:
        candidates.append((_first(getattr(obj, "seo_description", "")), "SEO field"))
        candidates.append((_first(getattr(obj, "summary", "")), "summary"))
        for name in ("body", "content"):
            candidates.append((_first(getattr(obj, name, "")), f"opening of {name}"))
    for text, basis in candidates:
        if len(text) >= DESCRIPTION_MIN:
            return Proposal(DESCRIPTION_FIELD, fit_description(text), basis)
    return None


def propose(
    seo_field: str,
    label: str,
    pk: str,
    settings_row: SEOSettings,
    site_name: str,
) -> Proposal | None:
    obj = load_object(label, pk)
    if obj is None:
        return None
    if seo_field == TITLE_FIELD:
        return propose_title(label, obj, settings_row, site_name)
    if seo_field == DESCRIPTION_FIELD:
        return propose_description(label, obj)
    return None
