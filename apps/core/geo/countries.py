"""Country records derived from django-countries plus the calling-code table."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from apps.core.search.text import fold

from .phone_codes import COUNTRY_PHONE_CODES
from .timezones import LEGACY_ALIASES, TIMEZONE_COUNTRIES

_REGION_SUBTAG = re.compile(r"^[A-Za-z]{2}$")


@dataclass(frozen=True, slots=True)
class Country:
    code: str
    name: str
    calling_code: str

    @property
    def label(self) -> str:
        return f"{self.name} ({self.code})"

    @property
    def calling_label(self) -> str:
        return f"{self.label} {self.calling_code}".strip()

    @property
    def search_key(self) -> str:
        return fold(f"{self.name} {self.code} {self.calling_code}")


@lru_cache(maxsize=1)
def _records() -> tuple[Country, ...]:
    from django_countries import countries

    records = [
        Country(
            code=str(code),
            name=str(name),
            calling_code=COUNTRY_PHONE_CODES.get(code, ""),
        )
        for code, name in countries
    ]
    records.sort(key=lambda item: fold(item.name))
    return tuple(records)


@lru_cache(maxsize=1)
def _by_code() -> dict[str, Country]:
    return {record.code: record for record in _records()}


def all_countries() -> tuple[Country, ...]:
    """Every country, sorted by accent-folded name."""
    return _records()


def country_by_code(code: str | None) -> Country | None:
    return _by_code().get((code or "").strip().upper())


def country_label(code: str | None) -> str:
    """ "Pakistan (PK)" for a known code, otherwise an empty string."""
    country = country_by_code(code)
    return country.label if country else ""


def calling_code_label(
    calling_code: str | None, country_code: str | None = None
) -> str:
    """ "Pakistan (PK) +92" for a calling code, preferring ``country_code`` when shared."""
    value = (calling_code or "").strip()
    if not value:
        return ""
    preferred = country_by_code(country_code)
    if preferred and preferred.calling_code == value:
        return preferred.calling_label
    for record in _records():
        if record.calling_code == value:
            return record.calling_label
    return value


def country_from_timezone(timezone_name: str | None) -> str | None:
    """ISO country for an IANA zone such as ``Asia/Karachi``; None when unknown."""
    name = (timezone_name or "").strip()
    if not name or len(name) > 64:
        return None
    resolved = LEGACY_ALIASES.get(name, name)
    return TIMEZONE_COUNTRIES.get(resolved)


def country_from_language_tag(tag: str | None) -> str | None:
    """Region of a BCP 47 tag ("en-PK", "zh-Hans-CN"); None without a known region."""
    parts = (tag or "").replace("_", "-").split("-")
    for subtag in parts[1:]:
        if _REGION_SUBTAG.match(subtag) and country_by_code(subtag):
            return subtag.upper()
    return None


def _accept_language_tags(header: str) -> list[str]:
    """Language tags of an ``Accept-Language`` header, best quality first."""
    weighted: list[tuple[float, int, str]] = []
    for position, entry in enumerate(header.split(",")):
        tag, _, params = entry.partition(";")
        tag = tag.strip()
        if not tag or tag == "*":
            continue
        quality = 1.0
        params = params.strip()
        if params.startswith("q="):
            try:
                quality = float(params[2:])
            except ValueError:
                continue
        weighted.append((-quality, position, tag))
    return [tag for _quality, _position, tag in sorted(weighted)]


def country_from_accept_language(header: str | None) -> str | None:
    """Region of the best-weighted ``Accept-Language`` entry that carries one."""
    if not header or len(header) > 500:
        return None
    for tag in _accept_language_tags(header):
        region = country_from_language_tag(tag)
        if region:
            return region
    return None
