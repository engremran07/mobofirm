"""Autocomplete widgets of the staff console (replacement for select2).

``django.contrib.admin`` ships ``AutocompleteSelect`` widgets that render the
attributes select2 reads. The console renders the same server contract (the
``admin:autocomplete`` JSON endpoint, validated server-side as before) through a small
Alpine combobox (``console/widgets/autocomplete.html``) and drops the bundled
jQuery/select2 assets.

Wire it in with ``ConsoleAutocompleteMixin`` (a ``ModelAdmin``/inline mixin): fields
listed in ``autocomplete_fields`` then use these widgets.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.admin import widgets as admin_widgets
from django.contrib.admin.options import BaseModelAdmin
from django.db.models import ForeignKey, ManyToManyField
from django.http import HttpRequest

__all__ = [
    "ConsoleAutocompleteMixin",
    "ConsoleAutocompleteSelect",
    "ConsoleAutocompleteSelectMultiple",
]


class _ConsoleAutocomplete(forms.Select):
    """Context and (empty) media shared by the single and multiple widgets."""

    template_name = "console/widgets/autocomplete.html"

    @property
    def media(self) -> forms.Media:
        return forms.Media()

    def get_context(
        self, name: str, value: Any, attrs: dict[str, Any] | None
    ) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        widget_attrs = context["widget"]["attrs"]
        context["widget"]["combobox"] = {
            "url": widget_attrs.get("data-ajax--url", ""),
            "app_label": widget_attrs.get("data-app-label", ""),
            "model_name": widget_attrs.get("data-model-name", ""),
            "field_name": widget_attrs.get("data-field-name", ""),
            "multiple": bool(widget_attrs.get("multiple")),
            "clearable": widget_attrs.get("data-allow-clear") == "true",
        }
        return context


class ConsoleAutocompleteSelect(_ConsoleAutocomplete, admin_widgets.AutocompleteSelect):
    """Single-choice autocomplete for a foreign key."""


class ConsoleAutocompleteSelectMultiple(
    _ConsoleAutocomplete, admin_widgets.AutocompleteSelectMultiple
):
    """Multiple-choice autocomplete for a many-to-many field."""


class ConsoleAutocompleteMixin(BaseModelAdmin):
    """Use the console widgets for ``autocomplete_fields`` (mix into admin classes)."""

    def formfield_for_foreignkey(
        self, db_field: ForeignKey, request: HttpRequest, **kwargs: Any
    ) -> Any:
        if "widget" not in kwargs and db_field.name in self.get_autocomplete_fields(
            request
        ):
            kwargs["widget"] = ConsoleAutocompleteSelect(
                db_field, self.admin_site, using=kwargs.get("using")
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(
        self, db_field: ManyToManyField, request: HttpRequest, **kwargs: Any
    ) -> Any:
        if "widget" not in kwargs and db_field.name in self.get_autocomplete_fields(
            request
        ):
            kwargs["widget"] = ConsoleAutocompleteSelectMultiple(
                db_field, self.admin_site, using=kwargs.get("using")
            )
        return super().formfield_for_manytomany(db_field, request, **kwargs)
