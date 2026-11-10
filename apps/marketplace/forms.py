from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseFormSet, formset_factory

from apps.core.editor.forms import RichTextFormMixin
from apps.firmwares.models import Brand, Chipset

from .earnings import PAYOUT_METHODS
from .models import (
    MAX_BIO_LENGTH,
    MAX_SOCIAL_LINKS,
    MAX_SPECIALITIES,
    ContactPreference,
    Listing,
    SocialPlatform,
)
from .services import ApplicationData, SocialLinkData
from .validators import MAX_URL_LENGTH, validate_https_url

MIN_BIO_LENGTH = 40


class SellerApplicationForm(forms.Form):
    """The seller-specific part of onboarding; profile details live in ``users``."""

    bio = forms.CharField(
        label="What you offer and what makes your work unique",
        min_length=MIN_BIO_LENGTH,
        max_length=MAX_BIO_LENGTH,
        widget=forms.Textarea(attrs={"rows": 6, "maxlength": MAX_BIO_LENGTH}),
        help_text=f"At least {MIN_BIO_LENGTH} characters. Shown on your public seller page.",
    )
    brands = forms.ModelMultipleChoiceField(
        queryset=Brand.objects.all(), required=False, label="Brand specialities"
    )
    chipsets = forms.ModelMultipleChoiceField(
        queryset=Chipset.objects.all(), required=False, label="Chipset specialities"
    )
    website = forms.CharField(
        label="Website (optional)",
        required=False,
        max_length=MAX_URL_LENGTH,
        widget=forms.URLInput(attrs={"placeholder": "https://", "autocomplete": "url"}),
        help_text="Must start with https://.",
    )
    contact_preference = forms.ChoiceField(
        label="How should buyers reach you?",
        choices=ContactPreference.choices,
        initial=ContactPreference.SOCIAL,
        widget=forms.RadioSelect,
    )
    accept_terms = forms.BooleanField(
        label="I agree to the seller terms",
        error_messages={"required": "Accept the seller terms to apply."},
    )

    def _limited(self, name: str, label: str) -> list[Any]:
        items = list(self.cleaned_data.get(name) or [])
        if len(items) > MAX_SPECIALITIES:
            raise ValidationError(f"Choose at most {MAX_SPECIALITIES} {label}.")
        return items

    def clean_brands(self) -> list[Any]:
        return self._limited("brands", "brands")

    def clean_chipsets(self) -> list[Any]:
        return self._limited("chipsets", "chipsets")

    def clean_website(self) -> str:
        value = str(self.cleaned_data.get("website") or "").strip()
        return validate_https_url(value) if value else ""

    def selected(self, name: str) -> list[Any]:
        """Currently chosen objects, from submitted data or initial values."""
        field = self.fields[name]
        raw = self[name].value() or []
        pks = [str(pk) for pk in raw][:MAX_SPECIALITIES]
        if not isinstance(field, forms.ModelMultipleChoiceField) or (
            field.queryset is None
        ):
            return []
        try:
            return list(field.queryset.filter(pk__in=pks).order_by())
        except (ValueError, TypeError):
            return []


class SocialLinkForm(forms.Form):
    platform = forms.ChoiceField(
        label="Platform", choices=SocialPlatform.choices, required=False
    )
    url = forms.CharField(
        label="Link",
        required=False,
        max_length=MAX_URL_LENGTH,
        widget=forms.URLInput(attrs={"placeholder": "https://"}),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        url = str(cleaned.get("url") or "").strip()
        platform = str(cleaned.get("platform") or "")
        if not url:
            cleaned["url"] = ""
            return cleaned
        if not platform:
            platform = SocialPlatform.OTHER
            cleaned["platform"] = platform
        try:
            cleaned["url"] = validate_https_url(url, platform)
        except ValidationError as exc:
            self.add_error("url", exc)
        return cleaned


class BaseSocialLinkFormSet(BaseFormSet):
    def clean(self) -> None:
        seen: set[str] = set()
        for form in self.forms:
            url = str(form.cleaned_data.get("url") or "") if form.is_valid() else ""
            if not url:
                continue
            key = url.casefold().rstrip("/")
            if key in seen:
                raise ValidationError("Each social link can only be added once.")
            seen.add(key)


SocialLinkFormSet = formset_factory(
    SocialLinkForm,
    formset=BaseSocialLinkFormSet,
    extra=MAX_SOCIAL_LINKS,
    max_num=MAX_SOCIAL_LINKS,
    validate_max=True,
    absolute_max=MAX_SOCIAL_LINKS,
)


def social_links(links: BaseFormSet[SocialLinkForm]) -> list[SocialLinkData]:
    """Filled rows of a validated social link formset."""
    return [
        SocialLinkData(
            platform=str(form.cleaned_data["platform"]),
            url=str(form.cleaned_data["url"]),
        )
        for form in links.forms
        if form.cleaned_data.get("url")
    ]


def application_data(
    form: SellerApplicationForm, links: BaseFormSet[SocialLinkForm]
) -> ApplicationData:
    """Combine validated forms into the service payload."""
    cleaned = form.cleaned_data
    return ApplicationData(
        bio=str(cleaned["bio"]),
        website=str(cleaned["website"]),
        contact_preference=str(cleaned["contact_preference"]),
        brands=list(cleaned["brands"]),
        chipsets=list(cleaned["chipsets"]),
        social_links=social_links(links),
        accepted_terms=bool(cleaned["accept_terms"]),
    )


class PayoutRequestForm(forms.Form):
    """Ask staff to pay out earned credits; reviewed in the wallet admin."""

    credits = forms.IntegerField(
        label="Credits to pay out",
        min_value=1,
        widget=forms.NumberInput(attrs={"min": 1, "inputmode": "numeric"}),
    )
    method = forms.ChoiceField(label="Payout method", choices=PAYOUT_METHODS)
    destination = forms.CharField(
        label="Where should staff send it?",
        max_length=255,
        help_text="Account, address or handle for the method you chose.",
    )


class ListingForm(RichTextFormMixin, forms.ModelForm):
    rich_text_fields: ClassVar[Mapping[str, str]] = {"description": "listing"}

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
