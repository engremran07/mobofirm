"""Wallet forms: top-up and payout requests, ledger filters."""

from __future__ import annotations

from django import forms

from .models import LedgerEntry, LedgerReason, PayoutRequest


class TopUpForm(forms.Form):
    credits = forms.IntegerField(
        label="Credits to add",
        min_value=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "step": 1}),
    )


class PayoutForm(forms.Form):
    credits = forms.IntegerField(
        label="Credits to pay out",
        min_value=1,
        widget=forms.NumberInput(attrs={"inputmode": "numeric", "step": 1}),
    )
    method = forms.ChoiceField(
        label="Payout method", choices=PayoutRequest.Method.choices
    )
    destination = forms.CharField(
        label="Account or address",
        max_length=255,
        help_text="Where staff send the money: IBAN, PayPal email or wallet address.",
    )


class LedgerFilterForm(forms.Form):
    """Filters of the credit history: free text, direction and reason."""

    q = forms.CharField(
        label="Search history",
        required=False,
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "type": "search",
                "placeholder": "Note, reference or reason",
                "autocomplete": "off",
                "enterkeyhint": "search",
                "data-live-search": "",
            }
        ),
    )
    direction = forms.ChoiceField(
        label="Type",
        required=False,
        choices=[("", "All"), *LedgerEntry.Direction.choices],
    )
    reason = forms.ChoiceField(
        label="Reason",
        required=False,
        choices=[("", "Any reason"), *LedgerReason.choices],
    )
