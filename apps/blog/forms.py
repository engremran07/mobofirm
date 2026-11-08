from __future__ import annotations

from typing import Any, ClassVar

from django import forms

from apps.firmwares.models import Brand, Chipset
from apps.firmwares.models import Model as DeviceModel
from apps.tags.selectors import public_tags

from .access import can_set_premium_access
from .models import Category, GuideRequest, Post, PostAccess


class SearchForm(forms.Form):
    q = forms.CharField(
        label="Search articles",
        max_length=200,
        widget=forms.TextInput(
            attrs={"placeholder": "Search articles", "class": "form-control"}
        ),
    )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "slug", "parent")
        widgets: ClassVar[dict[str, Any]] = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "slug": forms.TextInput(attrs={"class": "form-control"}),
            "parent": forms.Select(attrs={"class": "form-select"}),
        }


class GuideRequestForm(forms.Form):
    """A member asks for a flashing method, unlock pattern, trick or guide."""

    title = forms.CharField(
        label="What do you need?",
        min_length=8,
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
        help_text="e.g. “Bypass FRP on Samsung A14 without a PC”.",
    )
    kind = forms.ChoiceField(
        label="Type of request",
        choices=GuideRequest.Kind.choices,
        initial=GuideRequest.Kind.GUIDE,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    description = forms.CharField(
        label="Details",
        min_length=20,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 5, "class": "form-control"}),
        help_text="The device state, what you tried and what a good answer covers.",
    )
    brand = forms.ModelChoiceField(
        queryset=Brand.objects.all(), required=False, widget=forms.HiddenInput
    )
    model = forms.ModelChoiceField(
        queryset=DeviceModel.objects.select_related("brand"),
        required=False,
        widget=forms.HiddenInput,
    )
    chipset = forms.ModelChoiceField(
        queryset=Chipset.objects.all(), required=False, widget=forms.HiddenInput
    )

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        return cleaned


class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = (
            "title",
            "summary",
            "body",
            "category",
            "tags",
            "access",
            "proposed_price",
            "allow_comments",
            "noindex",
            "seo_title",
            "seo_description",
            "canonical_url",
            "hero_image",
        )
        widgets: ClassVar[dict[str, Any]] = {
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "summary": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
            "body": forms.Textarea(attrs={"rows": 15, "class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "tags": forms.SelectMultiple(attrs={"class": "form-select", "size": 8}),
            "access": forms.Select(attrs={"class": "form-select"}),
            "proposed_price": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0.01"}
            ),
            "allow_comments": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "noindex": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "seo_title": forms.TextInput(attrs={"class": "form-control"}),
            "seo_description": forms.Textarea(
                attrs={"rows": 2, "class": "form-control"}
            ),
            "canonical_url": forms.URLInput(attrs={"class": "form-control"}),
            "hero_image": forms.URLInput(attrs={"class": "form-control"}),
        }
        labels: ClassVar[dict[str, str]] = {
            "access": "Who can read it",
            "proposed_price": "Proposed price (USD)",
        }
        help_texts: ClassVar[dict[str, str]] = {
            "hero_image": "Use a path on this site such as /media/cover.jpg; other hosts are blocked by the site's content security policy.",
            "access": "Free is open to everyone. Featured and paid show a teaser to readers without access.",
            "proposed_price": "Needed for paid posts. Staff approve the final price when they publish.",
        }

    def __init__(self, *args, user=None, require_access: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        allowed = public_tags()
        if self.instance.pk:
            allowed = allowed | self.instance.tags.all()
        tags_field = self.fields["tags"]
        assert isinstance(tags_field, forms.ModelMultipleChoiceField)
        tags_field.queryset = allowed.distinct().order_by("name")
        self.premium_allowed = can_set_premium_access(user)
        choices = [
            (value, label)
            for value, label in PostAccess.choices
            if self.premium_allowed or value == PostAccess.FREE
        ]
        access_field = self.fields["access"]
        assert isinstance(access_field, forms.ChoiceField)
        if require_access:
            # A guide solution must state its access explicitly: no silent default.
            access_field.choices = [("", "Choose who can read it"), *choices]
            access_field.required = True
            access_field.initial = ""
            self.initial.pop("access", None)
            self.fields[
                "proposed_price"
            ].help_text = "Needed for paid solutions; staff approve the final price."
        else:
            access_field.choices = choices
            access_field.required = False
        if not self.premium_allowed:
            del self.fields["proposed_price"]

    def clean(self):
        super().clean()
        cleaned = self.cleaned_data
        access = cleaned.get("access") or PostAccess.FREE
        cleaned["access"] = access
        if access != PostAccess.FREE and not self.premium_allowed:
            self.add_error(
                "access", "Only approved authors can publish featured or paid posts."
            )
        price = cleaned.get("proposed_price")
        if access == PostAccess.PAID and self.premium_allowed and price is None:
            self.add_error("proposed_price", "A paid post needs a proposed price.")
        if access == PostAccess.FREE:
            cleaned["proposed_price"] = None
        return cleaned
