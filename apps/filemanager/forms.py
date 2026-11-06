from __future__ import annotations

from django import forms

EXPIRY_CHOICES = (
    ("", "Never expires"),
    ("1", "1 hour"),
    ("24", "1 day"),
    ("168", "7 days"),
    ("720", "30 days"),
    ("2160", "90 days"),
)


class FolderForm(forms.Form):
    name = forms.CharField(max_length=255)


class RenameForm(forms.Form):
    name = forms.CharField(max_length=255)


class ShareForm(forms.Form):
    expires_hours = forms.IntegerField(
        label="Expires",
        min_value=1,
        max_value=24 * 365,
        required=False,
        widget=forms.Select(choices=EXPIRY_CHOICES),
    )
    max_downloads = forms.IntegerField(
        label="Download limit", min_value=1, required=False
    )
    password = forms.CharField(
        max_length=128, required=False, widget=forms.PasswordInput(render_value=False)
    )


class SharePasswordForm(forms.Form):
    password = forms.CharField(
        max_length=128, required=False, widget=forms.PasswordInput(render_value=False)
    )


class TagsForm(forms.Form):
    tags = forms.CharField(
        label="Tags",
        max_length=500,
        required=False,
        help_text="Separate tags with commas. Tags are private to you.",
    )
