"""Location suggestions for onboarding forms (never persisted by themselves).

Signals, strongest first: the trusted edge/CDN country header, the browser's IANA
time zone, the region of the browser language, and the ``Accept-Language`` header.
No IP is sent to a third party and nothing here needs an IP at all, so it also
works on a local ``127.0.0.1`` development server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from django.http import HttpRequest

from apps.core.geo import (
    calling_code_label,
    country_by_code,
    country_from_accept_language,
    country_from_language_tag,
    country_from_timezone,
)
from apps.users.services.country_detection import detect_country

HintSource = Literal["edge", "timezone", "language", "accept-language"]

# Sources that only suggest where a visitor might be (a language is not a place), so
# a time zone or edge hint may replace a value they pre-filled.
WEAK_SOURCES: Final[frozenset[str]] = frozenset({"language", "accept-language"})


@dataclass(frozen=True, slots=True)
class LocationHint:
    country: str
    country_label: str
    calling_code: str
    calling_label: str
    source: HintSource

    @property
    def is_weak(self) -> bool:
        return self.source in WEAK_SOURCES

    def as_dict(self) -> dict[str, str]:
        return {
            "country": self.country,
            "country_label": self.country_label,
            "calling_code": self.calling_code,
            "calling_label": self.calling_label,
            "source": self.source,
        }


def _hint(country_code: str | None, source: HintSource) -> LocationHint | None:
    country = country_by_code(country_code)
    if country is None:
        return None
    return LocationHint(
        country=country.code,
        country_label=country.label,
        calling_code=country.calling_code,
        calling_label=calling_code_label(country.calling_code, country.code),
        source=source,
    )


def hint_from_request(request: HttpRequest) -> LocationHint | None:
    """Server-side suggestion: trusted edge header, then ``Accept-Language``."""
    edge = _hint(detect_country(request), "edge")
    if edge is not None:
        return edge
    return _hint(
        country_from_accept_language(request.headers.get("Accept-Language")),
        "accept-language",
    )


def hint_from_browser(
    timezone_name: str | None, language_tag: str | None
) -> LocationHint | None:
    """Client-reported zone (preferred) or language region, validated server side."""
    by_zone = _hint(country_from_timezone(timezone_name), "timezone")
    if by_zone is not None:
        return by_zone
    return _hint(country_from_language_tag(language_tag), "language")
