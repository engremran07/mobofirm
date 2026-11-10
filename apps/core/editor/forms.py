"""Form integration: a rich-text field and a mixin for existing forms.

Both sanitise with the field's profile on every submission and enforce its length
limit on the *visible* text, so markup never counts against the limit and nothing
the toolbar cannot produce is ever stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .sanitizer import sanitize_rich_text, text_length
from .spec import get_profile
from .widgets import RichTextWidget


def effective_limit(profile_limit: int, visible_limit: int = 0) -> int:
    """The stricter of the profile's limit and a field's own one (0 = unlimited)."""
    limits = [value for value in (profile_limit, visible_limit) if value]
    return min(limits) if limits else 0


def clean_rich_text(
    value: str | None,
    profile: str,
    *,
    required: bool = False,
    visible_limit: int = 0,
) -> str:
    """Sanitise ``value`` for ``profile`` and validate its length and presence.

    ``visible_limit`` is a field-specific cap on the visible text that applies on top
    of the profile's (the stricter wins).
    """
    spec = get_profile(profile)
    cleaned = sanitize_rich_text(value, spec, paragraphs=True)
    if required and not cleaned:
        raise ValidationError(_("This field is required."), code="required")
    limit = effective_limit(spec.layout.max_length, visible_limit)
    if limit and text_length(cleaned) > limit:
        raise ValidationError(
            _("Ensure this value has at most %(limit)d characters."),
            code="max_length",
            params={"limit": limit},
        )
    return cleaned


class RichTextField(forms.CharField):
    """A ``CharField`` edited with the rich-text editor of ``profile``.

    ``visible_limit`` caps the *visible* text (markup never counts), unlike
    ``max_length`` which would count the HTML itself and is therefore not used.
    """

    def __init__(self, *, profile: str, visible_limit: int = 0, **kwargs: Any) -> None:
        self.profile = profile
        self.visible_limit = visible_limit
        attrs = kwargs.pop("widget_attrs", None)
        kwargs.setdefault("widget", RichTextWidget(profile, attrs, visible_limit))
        super().__init__(**kwargs)

    def clean(self, value: Any) -> str:
        """Sanitise, then validate against the profile."""
        raw = super().clean(value)
        return clean_rich_text(
            raw,
            self.profile,
            required=self.required,
            visible_limit=self.visible_limit,
        )


class RichTextFormMixin(forms.BaseForm):
    """Give named fields of an existing form the editor of a profile.

    ``rich_text_fields`` maps a field name to its editor profile key. The widget is
    swapped in ``__init__`` (keeping the field's own attributes) and the submitted
    value is sanitised and length-checked in ``clean``. ``rich_text_when`` maps a
    field to ``(other field, value)``: the editor and the sanitising then only apply
    while that other field has that value (for example a page's HTML format).
    """

    rich_text_fields: ClassVar[Mapping[str, str]] = {}
    rich_text_when: ClassVar[Mapping[str, tuple[str, str]]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name, profile in self.rich_text_fields.items():
            field = self.fields.get(name)
            if field is None:
                continue
            attrs = {
                key: value
                for key, value in field.widget.attrs.items()
                if not key.startswith("data-mf-editor")
            }
            if name in self.rich_text_when:
                other, value = self.rich_text_when[name]
                attrs["data-mf-editor-when"] = f"{self.add_prefix(other)}={value}"
            field.widget = RichTextWidget(profile, attrs=attrs)
            field.widget.is_required = field.required

    def _rich_text_applies(self, name: str, cleaned: Mapping[str, Any]) -> bool:
        condition = self.rich_text_when.get(name)
        return condition is None or cleaned.get(condition[0]) == condition[1]

    def clean(self) -> dict[str, Any]:
        """Sanitise every rich-text field after the regular field cleaning."""
        cleaned = super().clean() or self.cleaned_data
        for name, profile in self.rich_text_fields.items():
            if name not in cleaned or name in self.errors:
                continue
            if not self._rich_text_applies(name, cleaned):
                continue
            field = self.fields[name]
            try:
                cleaned[name] = clean_rich_text(
                    cleaned[name], profile, required=field.required
                )
            except ValidationError as error:
                self.add_error(name, error)
        return cleaned
