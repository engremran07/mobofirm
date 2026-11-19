"""Changelist filters of the staff console (Django-only, no third-party admin theme).

Every class is a plain ``django.contrib.admin`` list filter, so the stock
``list_filter`` syntax keeps working::

    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
        ("owner", RelatedDropdownFilter),
        ("is_active", BooleanRadioFilter),
        ("title", FieldTextFilter),
        FreshnessFilter,  # a DropdownFilter / TextFilter subclass
    )

Template contract (templates are authored by the CRUD-template stream under
``templates/console/filters/``). Django's ``{% admin_list_filter cl spec %}`` renders
``spec.template`` with ``title``, ``choices`` (a list of
``{"selected", "query_string", "display"}`` dicts) and ``spec``:

=========================  ================================================
class                      template
=========================  ================================================
ChoicesDropdownFilter      ``console/filters/choices_dropdown.html``
BooleanRadioFilter         ``console/filters/choices_dropdown.html``
DropdownFilter             ``console/filters/choices_dropdown.html``
RelatedDropdownFilter      ``console/filters/related_dropdown.html``
RangeDateFilter            ``console/filters/range_date.html``
RangeDateTimeFilter        ``console/filters/range_datetime.html``
RangeNumericFilter         ``console/filters/range_numeric.html``
FieldTextFilter            ``console/filters/field_text.html``
TextFilter                 ``console/filters/text.html``
=========================  ================================================

Range filters expose ``spec.name_from`` / ``spec.name_to`` (``<field_path>_from`` and
``<field_path>_to`` query parameters), ``spec.value_from`` / ``spec.value_to``
(the raw strings currently applied), ``spec.input_type`` (``date``,
``datetime-local`` or ``number``) and ``spec.title``. Their single ``choices`` entry
is ``{"selected", "query_string" (clears the filter), "display": "All",
"hidden": [(name, value), ...]}`` where ``hidden`` are the other active query
parameters a GET form must carry along. Text filters expose
``spec.parameter_name`` (``<field_path>__icontains`` for ``FieldTextFilter``),
``spec.value`` (a method; templates call it implicitly) and the same single
``choices`` entry.

Malformed values (a hand-edited URL) raise Django's ``IncorrectLookupParameters`` so
the changelist redirects to ``?e=1`` instead of failing with a 500.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from django.contrib import admin
from django.contrib.admin.options import IncorrectLookupParameters
from django.contrib.admin.views.main import ChangeList
from django.db import models
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.translation import gettext_lazy as _

__all__ = [
    "BooleanRadioFilter",
    "ChoicesDropdownFilter",
    "DropdownFilter",
    "FieldTextFilter",
    "RangeDateFilter",
    "RangeDateTimeFilter",
    "RangeNumericFilter",
    "RelatedDropdownFilter",
    "TextFilter",
]

# Query parameters that never belong to a filter form's hidden carry-over.
_PAGINATION_PARAMS = frozenset({"p", "e"})


def _carry_over(changelist: ChangeList, own: tuple[str, ...]) -> list[tuple[str, str]]:
    """Other active query parameters, for a GET form that must keep them."""
    return [
        (name, value)
        for name, value in changelist.params.items()
        if name not in own and name not in _PAGINATION_PARAMS
    ]


def _first(value: object) -> str:
    """The query-string value as one string (Django hands lists to field filters)."""
    if isinstance(value, list | tuple):
        return str(value[-1]) if value else ""
    return "" if value is None else str(value)


class ChoicesDropdownFilter(admin.ChoicesFieldListFilter):
    """Field with ``choices``, offered as a dropdown."""

    template = "console/filters/choices_dropdown.html"


class BooleanRadioFilter(admin.BooleanFieldListFilter):
    """Yes / No (/ unknown) filter for boolean fields."""

    template = "console/filters/choices_dropdown.html"


class DropdownFilter(admin.SimpleListFilter):
    """Custom filter (``title``, ``parameter_name``, ``lookups``, ``queryset``)."""

    template = "console/filters/choices_dropdown.html"

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list:
        raise NotImplementedError("DropdownFilter subclasses must implement lookups().")

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        raise NotImplementedError(
            "DropdownFilter subclasses must implement queryset()."
        )


class RelatedDropdownFilter(admin.RelatedFieldListFilter):
    """Foreign key / many-to-many filter offered as a dropdown."""

    template = "console/filters/related_dropdown.html"


class _TextFilterMixin:
    """Shared ``choices`` of the free-text filters (one entry: the clear link)."""

    parameter_name: str | None
    used_parameters: dict[str, Any]

    def value(self) -> str | None:
        """The typed text, or ``None`` (same contract as ``SimpleListFilter.value``)."""
        text = _first(self.used_parameters.get(self.parameter_name or ""))
        return text or None

    def get_facet_counts(
        self, _pk_attname: str, _filtered_qs: QuerySet
    ) -> dict[str, Any]:
        """Free-text filters have no per-choice counts to show."""
        return {}

    def choices(self, changelist: ChangeList) -> Iterator[Any]:
        name = self.parameter_name or ""
        yield {
            "selected": not self.value(),
            "query_string": changelist.get_query_string(remove=[name]),
            "display": _("All"),
            "hidden": _carry_over(changelist, (name,)),
        }


class FieldTextFilter(_TextFilterMixin, admin.FieldListFilter):
    """Case-insensitive "contains" search on one field: ``("title", FieldTextFilter)``."""

    template = "console/filters/field_text.html"

    def __init__(
        self,
        field: models.Field,
        request: HttpRequest,
        params: dict[str, Any],
        model: type[models.Model],
        model_admin: admin.ModelAdmin,
        field_path: str,
    ) -> None:
        self.parameter_name = f"{field_path}__icontains"
        super().__init__(field, request, params, model, model_admin, field_path)

    def expected_parameters(self) -> list[str | None]:
        return [self.parameter_name]

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        value = (self.value() or "").strip()
        if not value:
            return queryset
        return queryset.filter(**{self.parameter_name or "": value})


class TextFilter(_TextFilterMixin, admin.SimpleListFilter):
    """Free-text filter whose ``queryset`` the subclass implements.

    Declare ``title`` and ``parameter_name``; read the typed text with ``self.value()``
    (``None`` when empty).
    """

    template = "console/filters/text.html"

    def lookups(self, request: HttpRequest, model_admin: admin.ModelAdmin) -> list:
        return []

    def has_output(self) -> bool:
        return True

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        raise NotImplementedError("TextFilter subclasses must implement queryset().")


class _RangeFilter(admin.FieldListFilter):
    """Inclusive ``from``/``to`` bounds on one field (``<path>_from``/``<path>_to``)."""

    input_type: ClassVar[str]
    template: str

    def __init__(
        self,
        field: models.Field,
        request: HttpRequest,
        params: dict[str, Any],
        model: type[models.Model],
        model_admin: admin.ModelAdmin,
        field_path: str,
    ) -> None:
        self.name_from = f"{field_path}_from"
        self.name_to = f"{field_path}_to"
        super().__init__(field, request, params, model, model_admin, field_path)

    @property
    def value_from(self) -> str:
        return _first(self.used_parameters.get(self.name_from)).strip()

    @property
    def value_to(self) -> str:
        return _first(self.used_parameters.get(self.name_to)).strip()

    def expected_parameters(self) -> list[str | None]:
        return [self.name_from, self.name_to]

    def parse_bound(self, raw: str, *, upper: bool) -> Any:
        """Convert one bound to a value for ``gte`` (lower) / ``lte`` or ``lt`` (upper)."""
        raise NotImplementedError

    def upper_lookup(self, _raw: str) -> str:
        return "lte"

    def queryset(self, request: HttpRequest, queryset: QuerySet) -> QuerySet:
        filters: dict[str, Any] = {}
        if self.value_from:
            filters[f"{self.field_path}__gte"] = self.parse_bound(
                self.value_from, upper=False
            )
        if self.value_to:
            filters[f"{self.field_path}__{self.upper_lookup(self.value_to)}"] = (
                self.parse_bound(self.value_to, upper=True)
            )
        return queryset.filter(**filters) if filters else queryset

    def get_facet_counts(
        self, _pk_attname: str, _filtered_qs: QuerySet
    ) -> dict[str, Any]:
        """Bounds are free-form, so there are no per-choice counts to show."""
        return {}

    def choices(self, changelist: ChangeList) -> Iterator[Any]:
        own = (self.name_from, self.name_to)
        yield {
            "selected": not (self.value_from or self.value_to),
            "query_string": changelist.get_query_string(remove=list(own)),
            "display": _("All"),
            "hidden": _carry_over(changelist, own),
        }


class RangeDateFilter(_RangeFilter):
    """Between two dates (inclusive) for a ``DateField``."""

    template = "console/filters/range_date.html"
    input_type = "date"

    def parse_bound(self, raw: str, *, upper: bool) -> date:
        try:
            parsed = parse_date(raw)
        except ValueError as exc:
            raise IncorrectLookupParameters(exc) from exc
        if parsed is None:
            raise IncorrectLookupParameters(f"Invalid date: {raw!r}")
        return parsed


class RangeDateTimeFilter(_RangeFilter):
    """Between two moments for a ``DateTimeField``.

    Bounds are ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM``. A date-only upper bound covers
    that whole day (the filter becomes "before the next midnight").
    """

    template = "console/filters/range_datetime.html"
    input_type = "datetime-local"

    def upper_lookup(self, raw: str) -> str:
        return "lt" if self._is_date_only(raw) else "lte"

    @staticmethod
    def _is_date_only(raw: str) -> bool:
        return "T" not in raw and " " not in raw

    def parse_bound(self, raw: str, *, upper: bool) -> datetime:
        try:
            if self._is_date_only(raw):
                day = parse_date(raw)
                if day is None:
                    raise IncorrectLookupParameters(f"Invalid date: {raw!r}")
                if upper:
                    day += timedelta(days=1)
                moment = datetime.combine(day, time.min)
            else:
                parsed = parse_datetime(raw)
                if parsed is None:
                    raise IncorrectLookupParameters(f"Invalid datetime: {raw!r}")
                moment = parsed
        except ValueError as exc:
            raise IncorrectLookupParameters(exc) from exc
        if timezone.is_naive(moment):
            return timezone.make_aware(moment)
        return moment


class RangeNumericFilter(_RangeFilter):
    """Between two numbers (inclusive) for integer, float and decimal fields."""

    template = "console/filters/range_numeric.html"
    input_type = "number"

    def parse_bound(self, raw: str, *, upper: bool) -> Decimal:
        try:
            number = Decimal(raw)
        except InvalidOperation as exc:
            raise IncorrectLookupParameters(f"Invalid number: {raw!r}") from exc
        if not number.is_finite():
            raise IncorrectLookupParameters(f"Invalid number: {raw!r}")
        return number
