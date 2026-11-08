"""Typed static geography used by pickers and location hints (no network calls)."""

from __future__ import annotations

from .countries import (
    Country,
    all_countries,
    calling_code_label,
    country_by_code,
    country_from_accept_language,
    country_from_language_tag,
    country_from_timezone,
    country_label,
)
from .phone_codes import COUNTRY_PHONE_CODES

__all__ = [
    "COUNTRY_PHONE_CODES",
    "Country",
    "all_countries",
    "calling_code_label",
    "country_by_code",
    "country_from_accept_language",
    "country_from_language_tag",
    "country_from_timezone",
    "country_label",
]
