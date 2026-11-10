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
        """The identifier a picker submits."""
        return self.value or self.title

    @property
    def picked_label(self) -> str:
        """The text a picker shows once the hit is chosen."""
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
    # Typo-tolerant second pass, requested when the strict pass found little.
    fuzzy: bool = False

    @property
    def is_staff(self) -> bool:
        """Whether the requesting user is staff."""
        return bool(getattr(self.user, "is_staff", False))

    @property
    def is_authenticated(self) -> bool:
        """Whether the requesting user is signed in."""
        return bool(getattr(self.user, "is_authenticated", False))


@dataclass(frozen=True, slots=True)
class VocabTerm:
    """One name a "did you mean" suggestion can point at.

    ``terms`` are the spellings compared with the query (best first); ``title``
    and ``url`` describe the entity the suggestion links to.
    """

    kind: str
    title: str
    url: str
    icon: str
    terms: tuple[str, ...]


class SearchProvider(ABC):
    """Contributes one kind of result. Register an instance with ``register``."""

    kind: ClassVar[str]
    label: ClassVar[str]
    label_plural: ClassVar[str]
    # Lower-case entity name used in "Did you mean X (brand)?" (defaults to label).
    noun: ClassVar[str] = ""
    # Heading of this kind's popular block ("Popular models"); empty = no block.
    popular_title: ClassVar[str] = ""
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
        """Whether ``user`` may see this provider's results."""
        if not self.staff_only:
            return True
        return bool(getattr(user, "is_staff", False))

    @property
    def entity_noun(self) -> str:
        """Lower-case entity name for suggestion labels."""
        return self.noun or self.label.lower()

    @abstractmethod
    def search(self, context: SearchContext) -> Iterable[SearchHit]:
        """Return hits for ``context`` (already filtered for the user)."""

    def vocabulary(self) -> Iterable[VocabTerm]:
        """Public names "did you mean" may suggest (none by default)."""
        return ()

    def watched_models(self) -> Iterable[type]:
        """Model classes whose changes invalidate the cached vocabulary."""
        return ()

    def popular(
        self, *, limit: int, window_days: int, scope: Mapping[str, str]
    ) -> Iterable[SearchHit]:
        """The most popular entries of this kind (none by default).

        ``window_days`` is the analytics window; ``scope`` narrows the list
        (``{"brand": slug}`` or ``{"chipset": slug}``). Implementations must only
        return public entries (same-site ``url`` paths) and avoid per-row queries.
        """
        del limit, window_days, scope
        return ()
