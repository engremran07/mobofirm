"""Forms for member catalog corrections and staff review."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from .catalog_update_proposals import TARGET_CONFIG
from .models import CatalogUpdateProposal


class CatalogUpdateValuesForm(forms.Form):
    """Only fields explicitly approved by the catalog proposal service are exposed."""

    def __init__(
        self,
        *args: Any,
        target_kind: str,
        initial_values: dict[str, Any] | None = None,
        changed_only: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        model_class, _, field_names = TARGET_CONFIG[target_kind]
        self.value_fields = frozenset(field_names)
        self.changed_only = changed_only
        initial_values = initial_values or {}
        for name in field_names:
            model_field = model_class._meta.get_field(name)
            if not isinstance(model_field, models.Field):
                raise ImproperlyConfigured(
                    f"Catalog proposal field {name!r} is not a forward model field."
                )
            initial = initial_values.get(name, "")
            is_aliases = (
                target_kind == CatalogUpdateProposal.TargetKind.CHIPSET_VENDOR
                and name == "aliases"
            )
            if is_aliases and isinstance(initial, list):
                initial = "\n".join(initial)
            self.fields[name] = forms.CharField(
                label=model_field.verbose_name.capitalize(),
                required=False,
                initial=initial,
                max_length=getattr(model_field, "max_length", None),
                widget=forms.Textarea(attrs={"rows": 4})
                if model_field.get_internal_type() == "TextField" or is_aliases
                else forms.TextInput(),
                help_text=(
                    "Enter one alias per line."
                    if is_aliases
                    else "Change the current value; leave it unchanged to omit it."
                ),
            )

    def proposed_values(self) -> dict[str, Any]:
        """Return non-empty fields from the validated form."""
        values = {
            name: value
            for name, value in self.cleaned_data.items()
            if name in self.value_fields
            and isinstance(value, str)
            and value.strip()
            and (
                not self.changed_only
                or value.strip() != str(self.fields[name].initial or "").strip()
            )
        }
        return values


class CatalogUpdateReviewForm(CatalogUpdateValuesForm):
    """Reviewer corrections plus a required reason on rejection."""

    decision = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject")),
        widget=forms.RadioSelect,
    )
    reason = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required when rejecting; optional note when approving.",
    )
