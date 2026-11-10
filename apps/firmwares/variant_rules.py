"""Variant identity rules shared by the catalogue, the admin, uploads and requests.

A model may have several variants (``AI2401``, ``AI2401_A``, ``AI2401_D``) or none
at all. This module owns the pure rules; see ``docs/04-backend/catalog-variants.md``
for the decision record.

- Codes are normalised (trimmed, whitespace collapsed to ``_``, upper-cased) so the
  same code never exists twice in different spellings.
- A variant is identified by at least one of: code, region/market, RAM/storage or
  carrier.
- A variant is required for an upload only when the chosen model has at least one
  active variant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CODE_MAX = 64
NO_VARIANT_LABEL = "No specific variant"
_MEMORY_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(tb|gb|mb)?", re.IGNORECASE)
_MEMORY_SPLIT = re.compile(r"\s*[/,|+]\s*|\s+and\s+", re.IGNORECASE)


@dataclass(frozen=True)
class VariantIdentity:
    """The fields that tell one variant of a model from another."""

    model_code: str = ""
    region: str = ""
    memory: str = ""
    carrier: str = ""

    @property
    def is_identified(self) -> bool:
        """True when at least one identifying field carries a value."""
        return any((self.model_code, self.region, self.memory, self.carrier))

    def label(self) -> str:
        """A readable name built from the identifying fields."""
        if self.model_code:
            extras = [part for part in (self.region, self.carrier) if part]
            return " · ".join([self.model_code, *extras])
        return " · ".join(
            part for part in (self.region, self.memory, self.carrier) if part
        )

    def normalized(self) -> VariantIdentity:
        """The same identity with a normalised code and cleaned free text."""
        return VariantIdentity(
            model_code=normalize_variant_code(self.model_code),
            region=clean_text(self.region, CODE_MAX),
            memory=clean_text(self.memory, CODE_MAX),
            carrier=clean_text(self.carrier, CODE_MAX),
        )


def normalize_variant_code(value: str | None) -> str:
    """``" ai2401 a "`` -> ``"AI2401_A"``; empty input stays empty."""
    text = "_".join((value or "").split())
    return text.upper()[:CODE_MAX]


def clean_text(value: str | None, limit: int) -> str:
    """Whitespace-collapsed ``value`` cut to ``limit`` characters."""
    return " ".join((value or "").split())[:limit]


def _gigabytes(number: str, unit: str | None) -> int | float:
    amount = float(number)
    unit = (unit or "gb").lower()
    if unit == "tb":
        amount *= 1024
    elif unit == "mb":
        amount /= 1024
    return int(amount) if amount == int(amount) else amount


def parse_memory(text: str | None) -> tuple[list[int | float], list[int | float]]:
    """Split ``"8 GB / 256 GB"`` into ``([8], [256])`` (gigabytes).

    The first figure is the RAM and the second the storage; text that cannot be
    read yields empty lists, so the label keeps the original wording.
    """
    parts = [part for part in _MEMORY_SPLIT.split(text or "") if part.strip()]
    values: list[int | float] = []
    for part in parts[:2]:
        match = _MEMORY_NUMBER.search(part)
        if match is None:
            return [], []
        values.append(_gigabytes(match.group(1), match.group(2)))
    if len(values) != 2:
        return [], []
    return [values[0]], [values[1]]


def variant_is_required(model: Any) -> bool:
    """A variant must be chosen only when ``model`` has an active variant."""
    if model is None:
        return False
    return bool(model.variants.filter(is_active=True).exists())
