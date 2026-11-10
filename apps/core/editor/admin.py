"""Admin integration: give chosen text fields of any ``ModelAdmin`` the editor."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, cast

from django.forms import ModelForm
from django.http import HttpRequest

from .forms import RichTextFormMixin


class RichTextAdminMixin:
    """Opt-in editor for named admin fields.

    List the mixin before the admin base class and set ``rich_text_fields`` to map a
    field name to its editor profile. ``rich_text_when`` maps a field to
    ``(other field, value)`` so the editor only applies while that field has the
    value (a page's HTML format, for example). The profile is resolved per request,
    so admin edits apply immediately.
    """

    rich_text_fields: ClassVar[Mapping[str, str]] = {}
    rich_text_when: ClassVar[Mapping[str, tuple[str, str]]] = {}

    def rich_text_attributes(self) -> dict[str, Any]:
        """The class attributes handed to the generated form class."""
        return {
            "rich_text_fields": self.rich_text_fields,
            "rich_text_when": self.rich_text_when,
        }

    def get_form(
        self,
        request: HttpRequest,
        obj: Any | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm]:
        """Return the admin form class with the rich-text mixin applied."""
        parent = cast("Any", super())
        base = parent.get_form(request, obj, change=change, **kwargs)
        return cast(
            "type[ModelForm]",
            type(
                base.__name__,
                (RichTextFormMixin, base),
                self.rich_text_attributes(),
            ),
        )
