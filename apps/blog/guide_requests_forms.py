"""Forms of the guide-request flow: the search step and the request form."""

from __future__ import annotations

from typing import Any

from django import forms

from apps.blog.guide_requests import (
    MAX_BOUNTY_OFFER,
    MAX_TEXT_LENGTH,
    MIN_TRIED_LENGTH,
    RequestContext,
    RequestText,
)
from apps.blog.models import GuideRequest, Post
from apps.core.editor.forms import RichTextField
from apps.core.editor.sanitizer import text_length
from apps.firmwares.models import Brand, Chipset, Variant
from apps.firmwares.models import Model as DeviceModel

GUIDE_REQUEST_PROFILE = "guide_request"


def _text(placeholder: str, **attrs: Any) -> forms.TextInput:
    return forms.TextInput(
        attrs={"class": "form-control", "placeholder": placeholder, **attrs}
    )


class GuideSearchForm(forms.Form):
    """Step 1: the device codes (at least one) and optional narrowing words."""

    model_code = forms.CharField(
        label="Model code",
        max_length=64,
        required=False,
        widget=_text("e.g. SM-A145F or X6836", autocomplete="off"),
        help_text="The code printed on the box or in Settings > About phone.",
    )
    variant_code = forms.CharField(
        label="Variant code",
        max_length=64,
        required=False,
        widget=_text("e.g. AI2401_A", autocomplete="off"),
        help_text="Regional or hardware variant code, if your model has one.",
    )
    brand = forms.CharField(
        label="Brand",
        max_length=64,
        required=False,
        widget=_text("e.g. Infinix", autocomplete="off"),
        help_text="Optional. Helps to list articles for the same brand.",
    )
    chipset = forms.CharField(
        label="Chipset",
        max_length=64,
        required=False,
        widget=_text("e.g. MediaTek Helio G99", autocomplete="off"),
        help_text="Optional. Also lists articles for devices with the same chipset.",
    )
    keywords = forms.CharField(
        label="Topic keywords",
        max_length=120,
        required=False,
        widget=_text("e.g. FRP bypass, scatter file, bootloader", autocomplete="off"),
        help_text="Optional. What you want to do, in a few words.",
    )

    def clean(self) -> dict[str, Any]:
        """Require a model code or a variant code."""
        cleaned = super().clean() or {}
        if not (cleaned.get("model_code") or cleaned.get("variant_code")):
            self.add_error(
                "model_code", "Enter the model code and/or the variant code."
            )
        return cleaned


class GuideRequestForm(forms.Form):
    """Step 2: ask for a flashing method, unlock pattern, trick or guide."""

    title = forms.CharField(
        label="What do you need?",
        min_length=8,
        max_length=200,
        widget=_text("e.g. Bypass FRP on Infinix Hot 40 without a PC"),
        help_text="One clear sentence, e.g. “Unlock the pattern on Samsung A14”.",
    )
    kind = forms.ChoiceField(
        label="Type of request",
        choices=GuideRequest.Kind.choices,
        initial=GuideRequest.Kind.METHOD,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Flashing method, unlock pattern, trick or a full guide.",
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
    model_code = forms.CharField(
        label="Model code",
        max_length=64,
        required=False,
        widget=_text("e.g. SM-A145F or X6836"),
        help_text="Required unless you picked the model above or enter a variant code.",
    )
    variant_code = forms.CharField(
        label="Variant code",
        max_length=64,
        required=False,
        widget=_text("e.g. AI2401_A"),
        help_text="Required unless you entered the model code.",
    )
    tried = RichTextField(
        profile=GUIDE_REQUEST_PROFILE,
        visible_limit=MAX_TEXT_LENGTH,
        label="What have you tried?",
        widget_attrs={
            "rows": 6,
            "class": "form-control",
            "placeholder": (
                "e.g. Flashed the stock ROM with SP Flash Tool v5.2316 in "
                "download mode, got error 0xC0020036 at 12%. Firmware "
                "version X6836-H891. USB 2.0 port, drivers reinstalled."
            ),
        },
        help_text=(
            "Required, at least "
            f"{MIN_TRIED_LENGTH} characters. Tools used, steps, error messages and "
            "the firmware version help writers give you a working answer."
        ),
    )
    expected_outcome = forms.CharField(
        label="Expected outcome",
        max_length=300,
        required=False,
        widget=_text("e.g. Phone boots to the setup wizard with FRP removed"),
        help_text="Optional. What a working answer should achieve, in one sentence.",
    )
    bounty_offer = forms.IntegerField(
        label="Bounty offer (credits)",
        min_value=1,
        max_value=MAX_BOUNTY_OFFER,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "e.g. 100", "min": "1"}
        ),
        help_text=(
            "Optional. Credits you plan to offer once staff approve the request; "
            "you place the bounty from the request page."
        ),
    )
    description = RichTextField(
        profile=GUIDE_REQUEST_PROFILE,
        visible_limit=MAX_TEXT_LENGTH,
        label="Extra details",
        required=False,
        widget_attrs={
            "rows": 3,
            "class": "form-control",
            "placeholder": "e.g. Bought second-hand, region lock suspected.",
        },
        help_text="Optional. Anything else a writer should know.",
    )
    related_post = forms.ModelChoiceField(
        queryset=Post.objects.none(), required=False, widget=forms.HiddenInput
    )
    search_keywords = forms.CharField(
        max_length=200, required=False, widget=forms.HiddenInput
    )

    def __init__(self, *args: Any, focus_tried: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        post_field = self.fields["related_post"]
        if isinstance(post_field, forms.ModelChoiceField):
            post_field.queryset = Post.objects.live()
        if focus_tried:
            self.fields["tried"].widget.attrs["autofocus"] = True

    def clean_tried(self) -> str:
        """The visible text (not the markup) must reach the minimum length."""
        value = str(self.cleaned_data["tried"])
        if text_length(value) < MIN_TRIED_LENGTH:
            raise forms.ValidationError(
                f"Describe what you tried in at least {MIN_TRIED_LENGTH} characters.",
                code="min_length",
            )
        return value

    def clean(self) -> dict[str, Any]:
        """Check the catalogue picks and link typed codes to catalogue rows."""
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        if not (
            cleaned.get("model")
            or cleaned.get("model_code")
            or cleaned.get("variant_code")
        ):
            self.add_error("model_code", "Enter the model code or the variant code.")
        return cleaned

    def to_text(self) -> RequestText:
        """The written part of a valid form."""
        data = self.cleaned_data
        return RequestText(
            title=data["title"],
            kind=data["kind"],
            tried=data["tried"],
            description=data.get("description", ""),
            expected_outcome=data.get("expected_outcome", ""),
            search_keywords=data.get("search_keywords", ""),
        )

    def to_context(self) -> RequestContext:
        """The device and article of a valid form; typed codes link to the catalogue."""
        data = self.cleaned_data
        model, variant = data.get("model"), None
        variant_code = data.get("variant_code", "")
        if variant_code:
            matches = Variant.objects.filter(model_code__iexact=variant_code)
            if model is not None:
                matches = matches.filter(model=model)
            found = list(matches.select_related("model")[:2])
            variant = found[0] if len(found) == 1 else None
        model_code = data.get("model_code", "")
        if model is None and model_code:
            found_models = list(
                DeviceModel.objects.filter(model_code__iexact=model_code)[:2]
            )
            model = found_models[0] if len(found_models) == 1 else None
        return RequestContext(
            brand=data.get("brand"),
            firmware_model=model,
            variant=variant,
            chipset=data.get("chipset"),
            model_code=model_code,
            variant_code=variant_code,
            related_post=data.get("related_post"),
        )
