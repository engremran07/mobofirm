from __future__ import annotations

from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator

from apps.bounty import services, targets


class PostBountyForm(forms.Form):
    kind = forms.ChoiceField(
        choices=[
            (targets.KIND_REQUEST, "Request"),
            (targets.KIND_MODEL, "Model"),
            (targets.KIND_VARIANT, "Variant"),
            (targets.KIND_GUIDE, "Guide request"),
        ],
        widget=forms.HiddenInput,
    )
    object_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    credits = forms.IntegerField(
        label="Reward (credits)",
        validators=[
            MinValueValidator(services.MIN_CREDITS),
            MaxValueValidator(services.MAX_CREDITS),
        ],
        help_text=f"{services.MIN_CREDITS}-{services.MAX_CREDITS} credits, held in escrow until someone fulfils it.",
    )
    days = forms.IntegerField(
        label="Stays open for (days)",
        initial=services.DEFAULT_DAYS,
        min_value=1,
        max_value=services.MAX_DAYS,
        help_text="Unclaimed credits return to you when it expires.",
    )
    note = forms.CharField(
        label="Details for contributors",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class ClaimForm(forms.Form):
    note = forms.CharField(label="Note (optional)", required=False, max_length=300)


class ReportForm(forms.Form):
    reason = forms.CharField(
        label="What is wrong?",
        min_length=10,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
