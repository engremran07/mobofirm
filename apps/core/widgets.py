"""Form widgets shared by the apps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django import forms

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise


class ComboboxSelect(forms.Select):
    """A ``Select`` rendered by ``components/combobox.html`` (type-ahead picker).

    ``kinds`` names the search kinds that feed the options (``country`` or
    ``calling_code`` for the built-in providers). The field keeps its ``choices``
    so the server still validates the submitted value. ``detected`` marks a value
    that came from location detection rather than the user, so a better client
    side hint may replace it until the user edits the field.
    """

    def __init__(
        self,
        *,
        kinds: str,
        placeholder: StrOrPromise = "",
        attrs: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(attrs=attrs)
        self.kinds = kinds
        self.placeholder = placeholder
        self.detected = False
