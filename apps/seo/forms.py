from __future__ import annotations

import secrets

from django import forms

from .models import SEOSettings


class SEOSettingsForm(forms.ModelForm):
    generate_indexnow_key = forms.BooleanField(
        required=False, label="Generate a new IndexNow key"
    )

    class Meta:
        model = SEOSettings
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if cleaned and cleaned.get("generate_indexnow_key"):
            cleaned["indexnow_key"] = secrets.token_hex(16)
            self.instance.indexnow_key = cleaned["indexnow_key"]
        return cleaned
