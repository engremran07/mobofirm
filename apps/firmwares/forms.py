"""Forms for the end-user request and upload pages."""

from __future__ import annotations

from typing import Any

from django import forms

from .models import (
    AccessTier,
    Brand,
    Chipset,
    FirmwarePackageFormat,
    Model,
    Variant,
)
from .services import max_upload_bytes
from .submissions import UploadSubmission, file_name_from_url
from .tracking_models import FIRMWARE_REQUEST_TYPES, FIRMWARE_REQUEST_URGENCIES


def _hidden_choice(queryset: Any, message: str) -> forms.ModelChoiceField:
    """A catalogue pick filled by the type-ahead combobox (hidden value)."""
    return forms.ModelChoiceField(
        queryset=queryset,
        widget=forms.HiddenInput,
        error_messages={"required": message, "invalid_choice": message},
    )


class BrandRequestForm(forms.Form):
    name = forms.CharField(
        label="Brand name", max_length=128, help_text="As printed on the device."
    )
    details = forms.CharField(
        label="Anything we should know?",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Website, country of origin or links that help us verify it.",
    )


class ChipsetRequestForm(forms.Form):
    vendor = forms.CharField(
        label="Vendor", max_length=64, help_text="e.g. MediaTek, Qualcomm, Unisoc"
    )
    name = forms.CharField(
        label="Chipset name", max_length=128, help_text="e.g. Helio G100 Ultra"
    )
    family = forms.CharField(
        label="Family", required=False, max_length=64, help_text="e.g. Helio"
    )
    aliases = forms.CharField(
        label="Other names",
        required=False,
        max_length=500,
        help_text="Alternative spellings or codes, separated by commas.",
    )
    details = forms.CharField(
        label="Why is it needed?",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Devices that use it, datasheet or vendor links.",
    )


class ModelRequestForm(forms.Form):
    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    chipset = forms.ModelChoiceField(
        queryset=Chipset.objects.all(),
        widget=forms.HiddenInput,
        required=False,
        error_messages={"invalid_choice": "Choose the chipset from the list."},
    )
    name = forms.CharField(label="Model name", max_length=128)
    model_code = forms.CharField(
        label="Model code", required=False, max_length=64, help_text="e.g. SM-S911B"
    )
    details = forms.CharField(
        label="Anything we should know?",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class VariantRequestForm(forms.Form):
    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = _hidden_choice(Model.objects.all(), "Choose the model from the list.")
    name = forms.CharField(
        label="Variant name", max_length=128, help_text="e.g. Global, EU, India"
    )
    model_code = forms.CharField(label="Model code", required=False, max_length=64)
    region = forms.CharField(label="Region", required=False, max_length=64)
    details = forms.CharField(
        label="Anything we should know?",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        return cleaned


class FirmwareRequestForm(forms.Form):
    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = forms.ModelChoiceField(
        queryset=Model.objects.all(), widget=forms.HiddenInput, required=False
    )
    variant_name = forms.CharField(
        label="Variant or region", required=False, max_length=200
    )
    firmware_type = forms.ChoiceField(
        label="Firmware type",
        choices=FIRMWARE_REQUEST_TYPES,
        initial="official",
    )
    urgency = forms.TypedChoiceField(
        label="Urgency",
        choices=FIRMWARE_REQUEST_URGENCIES,
        coerce=int,
        initial=2,
    )
    description = forms.CharField(
        label="Details",
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Build number, region, what you need it for.",
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        return cleaned


SOURCE_FILE = "file"
SOURCE_URL = "url"


class UploadForm(forms.Form):
    """Firmware upload: an exact variant plus a stored file or an external link."""

    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = _hidden_choice(Model.objects.all(), "Choose the model from the list.")
    variant = _hidden_choice(
        Variant.objects.all(),
        "Choose the variant from the list, or request it if it is missing.",
    )
    source = forms.ChoiceField(
        label="How do you provide the file?",
        choices=[
            (SOURCE_FILE, "Upload a file"),
            (SOURCE_URL, "Link to an external file"),
        ],
        widget=forms.RadioSelect,
        initial=SOURCE_FILE,
    )
    file = forms.FileField(label="Firmware file", required=False)
    external_url = forms.URLField(
        label="Download link",
        required=False,
        max_length=500,
        help_text="HTTPS link on an approved host; it must end with the file name.",
    )
    build_number = forms.CharField(label="Build number", max_length=128)
    android_version = forms.CharField(
        label="Android version", required=False, max_length=32
    )
    package_format = forms.ChoiceField(
        label="Package format",
        choices=FirmwarePackageFormat.choices,
        initial=FirmwarePackageFormat.UNKNOWN,
    )
    is_password_protected = forms.BooleanField(
        label="The archive is password protected", required=False
    )
    archive_password = forms.CharField(
        label="Archive password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Stored encrypted; shown only to reviewers.",
    )
    notes = forms.CharField(
        label="Notes for reviewers",
        required=False,
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    changelog = forms.CharField(
        label="Changelog",
        required=False,
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="What changed in this build.",
    )

    def __init__(self, *args: Any, seller: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seller = seller
        if seller:
            self.fields["access_tier"] = forms.ChoiceField(
                label="Access",
                choices=AccessTier.choices,
                initial=AccessTier.FREE,
                help_text="Paid and featured files are priced and confirmed by reviewers.",
            )
            self.fields["proposed_price"] = forms.DecimalField(
                label="Proposed price (USD)",
                required=False,
                min_value=0,
                max_digits=10,
                decimal_places=2,
                help_text="Paid files only. Reviewers set the final price.",
            )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        brand, model, variant = (
            cleaned.get("brand"),
            cleaned.get("model"),
            cleaned.get("variant"),
        )
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        elif model and variant and variant.model_id != model.pk:
            self.add_error(
                "variant", "This variant does not belong to the chosen model."
            )

        source = cleaned.get("source")
        if source == SOURCE_FILE:
            self._validate_file(cleaned.get("file"))
        elif source == SOURCE_URL:
            url = cleaned.get("external_url") or ""
            if not url:
                self.add_error("external_url", "Enter the download link.")
            elif not file_name_from_url(url):
                self.add_error(
                    "external_url", "The link must end with the firmware file name."
                )
        if cleaned.get("is_password_protected") and not cleaned.get("archive_password"):
            self.add_error("archive_password", "Enter the archive password.")
        if (
            self.seller
            and cleaned.get("access_tier") == AccessTier.PAID
            and not cleaned.get("proposed_price")
        ):
            self.add_error(
                "proposed_price", "Paid files need a proposed price above zero."
            )
        return cleaned

    def _validate_file(self, file: Any) -> None:
        if file is None:
            self.add_error("file", "Choose the firmware file to upload.")
        elif file.size > max_upload_bytes():
            limit_mb = max_upload_bytes() // (1024 * 1024)
            self.add_error("file", f"The file is larger than the {limit_mb} MB limit.")

    def to_submission(self) -> UploadSubmission:
        """The validated input; call only after ``is_valid()``."""
        data = self.cleaned_data
        if data["source"] == SOURCE_FILE:
            file, url = data["file"], ""
        else:
            file, url = None, data["external_url"]
        tier = data.get("access_tier") or AccessTier.FREE
        price = data.get("proposed_price") if tier == AccessTier.PAID else None
        return UploadSubmission(
            variant=data["variant"],
            build_number=data["build_number"].strip(),
            android_version=data["android_version"].strip(),
            package_format=data["package_format"],
            notes=data["notes"].strip(),
            changelog=data["changelog"].strip(),
            file=file,
            external_url=url,
            is_password_protected=data["is_password_protected"],
            archive_password=data["archive_password"],
            access_tier=tier,
            proposed_price=price,
        )
