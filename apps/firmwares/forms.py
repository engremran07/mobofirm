"""Forms for the end-user request and upload pages."""

from __future__ import annotations

from typing import Any

from django import forms

from apps.core.editor.forms import RichTextField

from .catalog_lookup import DETAILS_MAX
from .models import (
    AccessTier,
    Brand,
    Chipset,
    FirmwarePackageFormat,
    Model,
    Variant,
)
from .services import RELEASE_INFO_LIMITS, max_upload_bytes
from .submissions import UploadSubmission, file_name_from_url
from .tracking_models import FIRMWARE_REQUEST_TYPES, FIRMWARE_REQUEST_URGENCIES
from .variant_rules import VariantIdentity, variant_is_required


def _hidden_choice(queryset: Any, message: str) -> forms.ModelChoiceField:
    """A catalogue pick filled by the type-ahead combobox (hidden value)."""
    return forms.ModelChoiceField(
        queryset=queryset,
        widget=forms.HiddenInput,
        error_messages={"required": message, "invalid_choice": message},
    )


def _text(label: str, example: str, help_text: str, **kwargs: Any) -> forms.CharField:
    """A single-line request field with an example placeholder and help text."""
    return forms.CharField(
        label=label,
        help_text=help_text,
        widget=forms.TextInput(attrs={"placeholder": example}),
        **kwargs,
    )


def _notes(label: str, example: str, help_text: str) -> forms.CharField:
    """The optional formatted free-text field of a request, with an example."""
    return RichTextField(
        profile="request",
        visible_limit=DETAILS_MAX,
        label=label,
        required=False,
        widget_attrs={"rows": 3, "placeholder": example},
        help_text=help_text,
    )


class BrandRequestForm(forms.Form):
    """Ask for a manufacturer that is missing from the catalogue."""

    name = _text(
        "Brand name",
        "Xiaomi",
        "The manufacturer as printed on the device, e.g. Xiaomi, Samsung, Tecno.",
        max_length=128,
    )
    details = _notes(
        "Anything we should know?",
        "https://www.mi.com. Makes Redmi, POCO and Xiaomi phones.",
        "Website, country of origin or links that help us verify the brand.",
    )


class ChipsetRequestForm(forms.Form):
    """Ask for a system-on-chip that is missing from the chipset catalogue."""

    vendor = _text(
        "Vendor",
        "MediaTek",
        "The company that makes the chip: MediaTek, Qualcomm, Unisoc, Samsung.",
        max_length=64,
    )
    name = _text(
        "Chipset name",
        "Dimensity 9200 (MT6985)",
        "The chip name without the vendor; add the part number in brackets.",
        max_length=128,
    )
    family = _text(
        "Family",
        "Dimensity",
        "The product line, e.g. Dimensity, Helio, Snapdragon, Exynos.",
        required=False,
        max_length=64,
    )
    aliases = _text(
        "Other names",
        "MT6985, Dimensity 9200 5G",
        "Alternative spellings or part numbers, separated by commas.",
        required=False,
        max_length=500,
    )
    details = _notes(
        "Why is it needed?",
        "Used by the Vivo X90 and Oppo Find X6; datasheet at mediatek.com.",
        "Devices that use it, datasheet or vendor links.",
    )


class ModelRequestForm(forms.Form):
    """Ask for a device model that is not listed under its brand yet."""

    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    chipset = forms.ModelChoiceField(
        queryset=Chipset.objects.all(),
        widget=forms.HiddenInput,
        required=False,
        error_messages={"invalid_choice": "Choose the chipset from the list."},
    )
    name = _text(
        "Model name",
        "Redmi Note 13 Pro 5G",
        "The marketing name printed on the box, without the brand.",
        max_length=128,
    )
    model_code = _text(
        "Model code",
        "2312DRA50G",
        "The manufacturer's model number, found in Settings › About phone.",
        required=False,
        max_length=64,
    )
    details = _notes(
        "Anything we should know?",
        "Released in January 2024; sold in Europe as the Redmi Note 13 Pro 5G.",
        "Release date, regions, a link to the specifications.",
    )


class VariantRequestForm(forms.Form):
    """Ask for a regional or hardware variant of an existing model.

    The brand and model are required; so is at least one identifying field (the
    variant code, the region or market, RAM / storage or the carrier). The name is
    optional and is built from those fields when left empty.
    """

    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = _hidden_choice(Model.objects.all(), "Choose the model from the list.")
    name = _text(
        "Variant name",
        "AI2401_A",
        "Optional. How the variant should appear in lists; defaults to its code.",
        required=False,
        max_length=128,
    )
    model_code = _text(
        "Variant code",
        "AI2401_A",
        "The code printed on the box or in the build, e.g. AI2401, AI2401_A, "
        "AI2401_D. Saved in upper case.",
        required=False,
        max_length=64,
    )
    region = _text(
        "Region or market",
        "Global",
        "Where it is sold, e.g. Global, EU, India, China.",
        required=False,
        max_length=64,
    )
    memory = _text(
        "RAM / storage",
        "8 GB / 256 GB",
        "The memory configuration: RAM first, then storage.",
        required=False,
        max_length=64,
    )
    carrier = _text(
        "Carrier",
        "Verizon",
        "Only when the variant is locked to an operator.",
        required=False,
        max_length=64,
    )
    details = _notes(
        "Anything we should know?",
        "Sold in India only; the box says AI2401_A.",
        "Where you saw the code, build numbers or a link.",
    )

    def clean(self) -> dict[str, Any]:
        """Brand and model must match; one identifying field is required."""
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        identity = VariantIdentity(
            model_code=cleaned.get("model_code", ""),
            region=cleaned.get("region", ""),
            memory=cleaned.get("memory", ""),
            carrier=cleaned.get("carrier", ""),
        ).normalized()
        if brand and model and not identity.is_identified:
            self.add_error(
                None,
                "Give at least one of: the variant code, the region or market, "
                "RAM / storage, or the carrier.",
            )
        return cleaned


class FirmwareRequestForm(forms.Form):
    """Ask for a firmware build we do not publish yet."""

    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = forms.ModelChoiceField(
        queryset=Model.objects.all(), widget=forms.HiddenInput, required=False
    )
    variant_name = _text(
        "Variant or region",
        "AI2401_A or Global",
        "The variant code or the region of the device, if you know it.",
        required=False,
        max_length=200,
    )
    firmware_type = forms.ChoiceField(
        label="Firmware type",
        choices=FIRMWARE_REQUEST_TYPES,
        initial="official",
        help_text="Official is the stock build from the manufacturer; the others "
        "are engineering, readback and modified builds.",
    )
    urgency = forms.TypedChoiceField(
        label="Urgency",
        choices=FIRMWARE_REQUEST_URGENCIES,
        coerce=int,
        initial=2,
        help_text="How soon you need it; it helps reviewers prioritise.",
    )
    description = _notes(
        "Details",
        "V14.0.5.0.TMGMIXM, EEA region. Needed to unbrick after a failed flash.",
        "Build number, region and what you need it for.",
    )

    def clean(self) -> dict[str, Any]:
        """The chosen model must belong to the chosen brand."""
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error("model", "This model does not belong to the chosen brand.")
        return cleaned


SOURCE_FILE = "file"
SOURCE_URL = "url"


class TierSelect(forms.Select):
    """Access-tier select that renders some options disabled but still visible."""

    def __init__(
        self,
        attrs: dict[str, Any] | None = None,
        *,
        disabled_values: tuple[str, ...] = (),
    ) -> None:
        super().__init__(attrs)
        self.disabled_values = tuple(str(value) for value in disabled_values)

    def create_option(
        self, name: str, value: Any, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        option = super().create_option(name, value, *args, **kwargs)
        if str(value) in self.disabled_values:
            option["attrs"]["disabled"] = True
        return option


class UploadForm(forms.Form):
    """Firmware upload: an exact variant plus a stored file or an external link."""

    brand = _hidden_choice(Brand.objects.all(), "Choose the brand from the list.")
    model = _hidden_choice(Model.objects.all(), "Choose the model from the list.")
    variant = forms.ModelChoiceField(
        queryset=Variant.objects.all(),
        widget=forms.HiddenInput,
        required=False,
        error_messages={
            "invalid_choice": "Choose the variant from the list, or request it."
        },
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
    changelog = RichTextField(
        profile="firmware_notes",
        visible_limit=RELEASE_INFO_LIMITS["changelog"],
        label="Changelog",
        required=False,
        widget_attrs={"rows": 4},
        help_text="What changed in this build. Shown on the firmware page.",
    )

    def __init__(self, *args: Any, seller: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.seller = seller
        # Everyone sees the access control. Only approved sellers can leave Free:
        # for the others Featured and Paid are shown but disabled, and a forged
        # value is ignored in ``clean_access_tier``.
        locked = () if seller else (AccessTier.FEATURED, AccessTier.PAID)
        self.fields["access_tier"] = forms.ChoiceField(
            label="Access",
            required=seller,
            choices=AccessTier.choices,
            initial=AccessTier.FREE,
            widget=TierSelect(disabled_values=locked),
            help_text=(
                "Paid and featured files are priced and confirmed by reviewers."
                if seller
                else "Uploads are free. Featured and Paid are available to approved sellers."
            ),
        )
        if seller:
            self.fields["proposed_price"] = forms.DecimalField(
                label="Proposed price (USD)",
                required=False,
                min_value=0,
                max_digits=10,
                decimal_places=2,
                widget=forms.NumberInput(
                    attrs={
                        "inputmode": "decimal",
                        "data-msg-required": "Enter the amount for a paid file.",
                        "data-msg-min": "Enter an amount above zero.",
                        "data-msg-step": "Use at most two decimal places, for example 4.50.",
                    }
                ),
                help_text="Paid files only. Reviewers set the final price.",
            )
            # Django derives min="0" from min_value; a paid amount must be above zero.
            self.fields["proposed_price"].widget.attrs["min"] = "0.01"

    def clean_access_tier(self) -> str:
        """Only approved sellers may choose Featured or Paid; everyone else gets Free."""
        tier = self.cleaned_data.get("access_tier")
        return tier if self.seller and tier else AccessTier.FREE

    def clean(self) -> dict[str, Any]:
        """Validate the device (variant rules), the file source and the tier."""
        cleaned = super().clean() or {}
        self._validate_device(cleaned)
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

    def _validate_device(self, cleaned: dict[str, Any]) -> None:
        """Brand/model/variant must agree; a variant is needed only if the model has
        active variants (see ``variant_rules``)."""
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
        elif model and variant is None and variant_is_required(model):
            self.add_error(
                "variant",
                "This model has variants: choose the one that matches your "
                "device, or request it if it is missing.",
            )

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
            model=data["model"],
            variant=data.get("variant"),
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
