"""Pick-only search providers for countries and calling codes (static dataset)."""

from __future__ import annotations

from collections.abc import Iterable

from apps.core.geo import Country, all_countries
from apps.core.search import SearchContext, SearchHit, SearchProvider, register
from apps.core.search.text import score_text


class CountryProvider(SearchProvider):
    """Countries by name or ISO code; the value is the ISO 3166-1 alpha-2 code."""

    kind = "country"
    label = "Country"
    label_plural = "Countries"
    icon = "globe"
    priority = 900
    navigable = False
    min_length = 1
    supports_browse = True

    def _score(self, context: SearchContext, country: Country) -> float:
        return score_text(
            context.query,
            context.tokens,
            country.name,
            country.code,
            country.calling_code,
        )

    def _hit(self, country: Country, score: float) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=country.name,
            subtitle=country.code,
            icon=self.icon,
            score=score,
            value=country.code,
            label=country.label,
            extra=(("calling-code", country.calling_code),),
        )

    def _candidates(self) -> Iterable[Country]:
        return all_countries()

    def search(self, context: SearchContext) -> Iterable[SearchHit]:
        if not context.query:
            return [self._hit(country, 0.0) for country in self._candidates()][
                : context.limit
            ]
        scored = (
            (self._score(context, country), country) for country in self._candidates()
        )
        return [self._hit(country, score) for score, country in scored if score > 0]


class CallingCodeProvider(CountryProvider):
    """Calling codes as "Pakistan (PK) +92"; the value is the code itself."""

    kind = "calling_code"
    label = "Calling code"
    label_plural = "Calling codes"
    icon = "phone"
    priority = 910

    def _candidates(self) -> Iterable[Country]:
        return [country for country in all_countries() if country.calling_code]

    def _hit(self, country: Country, score: float) -> SearchHit:
        return SearchHit(
            kind=self.kind,
            title=country.label,
            subtitle=country.calling_code,
            icon=self.icon,
            score=score,
            value=country.calling_code,
            label=country.calling_label,
            extra=(("country", country.code),),
        )


register(CountryProvider())
register(CallingCodeProvider())
