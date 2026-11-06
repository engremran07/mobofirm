"""Admin form: write-only secret inputs on top of the ``SiteSettings`` fields."""

from __future__ import annotations

from typing import Any

from django import forms
from unfold.widgets import UnfoldAdminPasswordWidget

from .models import SECRET_NAMES, SecretName, SiteSettings

SECRET_LABELS: dict[SecretName, str] = {
    "gmail_app_password": "Gmail app password",
    "smtp_password": "SMTP password",
    "recaptcha_private_key": "reCAPTCHA private key",
}
SECRET_MAX_LENGTH = 256


def _secret_input() -> forms.CharField:
    return forms.CharField(
        required=False,
        strip=False,
        max_length=SECRET_MAX_LENGTH,
        # render_value=False: a stored secret is never sent back to the browser.
        widget=UnfoldAdminPasswordWidget(
            attrs={"autocomplete": "new-password"}, render_value=False
        ),
    )


class SiteSettingsAdminForm(forms.ModelForm):
    gmail_app_password = _secret_input()
    clear_gmail_app_password = forms.BooleanField(required=False)
    smtp_password = _secret_input()
    clear_smtp_password = forms.BooleanField(required=False)
    recaptcha_private_key = _secret_input()
    clear_recaptcha_private_key = forms.BooleanField(required=False)

    class Meta:
        model = SiteSettings
        fields = "__all__"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in SECRET_NAMES:
            stored = self.instance.pk is not None and self.instance.has_secret(name)
            field = self.fields[name]
            field.label = SECRET_LABELS[name]
            field.help_text = (
                "A value is stored (encrypted). Leave empty to keep it, or type a "
                "new value to replace it."
                if stored
                else "Stored encrypted and never displayed again."
            )
            clear = self.fields[f"clear_{name}"]
            clear.label = f"Remove the stored {SECRET_LABELS[name].lower()}"

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        for name in SECRET_NAMES:
            if cleaned.get(f"clear_{name}"):
                self.instance.set_secret(name, "")
            elif cleaned.get(name):
                self.instance.set_secret(name, cleaned[name])
        return cleaned

    def changed_secret_names(self) -> list[str]:
        return [
            SECRET_LABELS[name]
            for name in SECRET_NAMES
            if self.cleaned_data.get(name) or self.cleaned_data.get(f"clear_{name}")
        ]
