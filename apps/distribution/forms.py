from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from unfold.widgets import (
    UnfoldAdminPasswordWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextInputWidget,
)

from apps.distribution import channels, services
from apps.distribution.models import Channel

CRED_PREFIX = "cred_"
_FORM_CACHE: dict[str, type[ChannelAdminForm]] = {}


def cred_name(field_name: str) -> str:
    return f"{CRED_PREFIX}{field_name}"


class ChannelAdminForm(forms.ModelForm):
    """The credentials are write-only: stored encrypted, never shown again.

    The platform-specific fields (``cred_*``) are declared on a subclass built per
    platform by ``channel_form_for`` so the admin renders exactly what the platform
    needs.
    """

    clear_secret = forms.BooleanField(
        required=False, label="Remove the stored credentials"
    )

    class Meta:
        model = Channel
        fields = "__all__"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        adapter = self._adapter()
        if "kind" in self.fields and self.instance.pk is None:
            self.fields["kind"].widget = forms.HiddenInput()
        if adapter is None or self.instance.pk is None:
            return
        stored = services.stored_values(self.instance)
        for spec in adapter.fields:
            name = cred_name(spec.name)
            if name not in self.fields:
                continue
            if spec.is_secret:
                if stored.get(spec.name):
                    self.fields[name].help_text = (
                        "A credential is stored (encrypted). Leave empty to keep it, "
                        "or type a new one to replace it. " + spec.help
                    )
            else:
                self.initial[name] = stored.get(spec.name, "")

    def _adapter(self) -> channels.Adapter | None:
        kind = self.instance.kind or self.initial.get("kind") or self.data.get("kind")
        return channels.get_adapter(str(kind or ""))

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        kind = cleaned.get("kind") or self.instance.kind
        adapter = channels.get_adapter(str(kind or ""))
        if adapter is None:
            raise ValidationError("Choose a platform first.")
        # Applied before model validation so "enabled" is checked against them.
        self.instance.kind = str(kind)
        values = {
            spec.name: str(cleaned.get(cred_name(spec.name)) or "")
            for spec in adapter.fields
        }
        services.apply_values(
            self.instance, values, clear_secrets=bool(cleaned.get("clear_secret"))
        )
        return cleaned


def channel_form_for(kind: str) -> type[ChannelAdminForm]:
    """The channel form with the credential fields of platform ``kind``."""
    adapter = channels.get_adapter(kind)
    if adapter is None:
        return ChannelAdminForm
    cached = _FORM_CACHE.get(kind)
    if cached is not None:
        return cached
    declared: dict[str, forms.Field] = {}
    for spec in adapter.fields:
        attrs = {"autocomplete": "off", "placeholder": spec.placeholder}
        if spec.choices:
            field: forms.Field = forms.ChoiceField(
                choices=[("", "—"), *spec.choices],
                required=False,
                label=spec.label,
                help_text=spec.help,
                widget=UnfoldAdminSelectWidget,
            )
        elif spec.is_secret:
            field = forms.CharField(
                required=False,
                strip=False,
                max_length=2000,
                label=spec.label,
                help_text=spec.help,
                widget=UnfoldAdminPasswordWidget(
                    attrs={"autocomplete": "new-password"}, render_value=False
                ),
            )
        else:
            field = forms.CharField(
                required=False,
                max_length=500,
                label=spec.label,
                help_text=spec.help,
                widget=UnfoldAdminTextInputWidget(attrs=attrs),
            )
        declared[cred_name(spec.name)] = field
    form = type(f"{kind.title()}ChannelForm", (ChannelAdminForm,), declared)
    _FORM_CACHE[kind] = form
    return form
