from __future__ import annotations

from typing import Any

from django import forms

from apps.consent.constants import (
    ACTION_ACCEPT_ALL,
    ACTION_CUSTOM,
    ACTION_REJECT_ALL,
    SOURCE_BANNER,
    SOURCE_PRIVACY_CENTRE,
)


class ConsentChoiceForm(forms.Form):
    """The banner and the privacy centre post the same fields."""

    action = forms.ChoiceField(
        choices=[
            (ACTION_ACCEPT_ALL, "Accept all"),
            (ACTION_REJECT_ALL, "Reject optional"),
            (ACTION_CUSTOM, "Save my choices"),
        ]
    )
    categories = forms.MultipleChoiceField(required=False, choices=[])
    source = forms.ChoiceField(
        required=False,
        choices=[(SOURCE_BANNER, "Banner"), (SOURCE_PRIVACY_CENTRE, "Privacy centre")],
    )
    next = forms.CharField(required=False, max_length=500)

    def __init__(self, *args: Any, optional_slugs: list[str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["categories"]
        if isinstance(field, forms.MultipleChoiceField):
            field.choices = [(slug, slug) for slug in optional_slugs]
