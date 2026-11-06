from __future__ import annotations

from typing import Any, ClassVar

from django import forms

from .models import MAX_BIO_LENGTH, MAX_DISPLAY_NAME_LENGTH, Listing, SellerProfile


class SellerProfileForm(forms.ModelForm):
    class Meta:
        model = SellerProfile
        fields = ("display_name", "bio")
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "bio": forms.Textarea(attrs={"rows": 4, "maxlength": MAX_BIO_LENGTH})
        }

    def clean_display_name(self) -> str:
        name = self.cleaned_data["display_name"].strip()
        if not name:
            raise forms.ValidationError("Enter a display name.")
        if len(name) > MAX_DISPLAY_NAME_LENGTH:
            raise forms.ValidationError("Display name is too long.")
        return name


class ListingForm(forms.ModelForm):
    class Meta:
        model = Listing
        fields = ("title", "description", "price", "discount_price")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        price = cleaned.get("price")
        discount = cleaned.get("discount_price")
        if price is not None and discount is not None and discount > price:
            self.add_error("discount_price", "Discount price cannot exceed the price.")
        return cleaned


class VerificationRequestForm(forms.Form):
    evidence = forms.CharField(
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Public links or a description that lets staff confirm who you are.",
    )
