"""Value types of the central search engine."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One result row.

    ``url`` is a same-site path (empty for pick-only kinds such as countries).
    ``value`` is the identifier a picker submits and ``label`` the text it shows
    once chosen; both default to the title. ``extra`` becomes ``data-*`` attributes
    on the suggestion option so a picker can react to a choice.
    """

    kind: str
    title: str
    subtitle: str = ""
    url: str = ""
    icon: str = "search"
    score: float = 0.0
    value: str = ""
    label: str = ""
    extra: tuple[tuple[str, str], ...] = ()

    @property
    def picked_value(self) -> str:
        return self.value or self.title

    @property
    def picked_label(self) -> str:
        return self.label or self.title


@dataclass(frozen=True, slots=True)
class SearchContext:
    """What a provider receives for one search."""

    query: str
    tokens: tuple[str, ...]
    limit: int
    user: object | None = None
    # Provider-specific narrowing taken from ``filter.<name>`` request parameters,
    # for example ``{"brand": "samsung"}`` for a model picker.
    filters: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_staff(self) -> bool:
        return bool(getattr(self.user, "is_staff", False))

    @property
    def is_authenticated(self) -> bool:
        return bool(getattr(self.user, "is_authenticated", False))


class SearchProvider(ABC):
    """Contributes one kind of result. Register an instance with ``register``."""

    kind: ClassVar[str]
    label: ClassVar[str]
    label_plural: ClassVar[str]
    icon: ClassVar[str] = "search"
    # Lower numbers are listed first when scores tie and in grouped results.
    priority: ClassVar[int] = 100
    # Hidden from everyone but staff.
    staff_only: ClassVar[bool] = False
    # Included when no ``kinds`` are requested (header and full page search).
    # Pick-only kinds (countries) set this to False.
    navigable: ClassVar[bool] = True
    # Smallest query a user must type before this provider is asked.
    min_length: ClassVar[int] = 2
    # May answer an empty query with a browse list (pickers only).
    supports_browse: ClassVar[bool] = False

    def is_visible_to(self, user: object | None) -> bool:
        if not self.staff_only:
            return True
        return bool(getattr(user, "is_staff", False))

    @abstractmethod
    def search(self, context: SearchContext) -> Iterable[SearchHit]:
        """Return hits for ``context`` (already filtered for the user)."""
