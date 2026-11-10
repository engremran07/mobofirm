"""End-user firmware submission: one exact variant, a file or an allowlisted link.

The upload page and any other first-party caller go through
:func:`submit_user_upload`; the staging, validation, tier rules and review
workflow are the existing firmware services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from django.core.exceptions import PermissionDenied

from .access import is_seller
from .catalog_requests import has_verified_email
from .models import (
    AccessTier,
    FirmwarePackageFormat,
    Model,
    PendingFirmware,
    Variant,
)
from .services import (
    FirmwareUploadError,
    attempt_extraction,
    check_submission_rate,
    handle_upload,
    run_ai_analysis,
    submit_external_firmware,
)
from .variant_rules import variant_is_required


@dataclass(frozen=True)
class UploadSubmission:
    """Validated input of the upload page."""

    model: Model
    build_number: str
    variant: Variant | None = None
    android_version: str = ""
    package_format: str = FirmwarePackageFormat.UNKNOWN
    notes: str = ""
    changelog: str = ""
    file: Any = None
    external_url: str = ""
    is_password_protected: bool = False
    archive_password: str = ""
    access_tier: str = AccessTier.FREE
    proposed_price: Any = None


def file_name_from_url(url: str) -> str:
    """The last path segment of a download URL, used as the file name."""
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1])


def can_offer_paid(user: Any) -> bool:
    """Whether the upload form may show the paid/featured fields."""
    return is_seller(user)


def submit_user_upload(user: Any, submission: UploadSubmission) -> PendingFirmware:
    """Create the pending submission for the model (and variant) and analyse it."""
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_active
        and has_verified_email(user)
    ):
        raise PermissionDenied("Sign in with a verified email to upload firmware.")
    check_submission_rate(user)
    variant = submission.variant
    model = submission.model
    if variant is not None and variant.model_id != model.pk:
        raise FirmwareUploadError("The variant does not belong to the model.")
    if variant is None and variant_is_required(model):
        raise FirmwareUploadError(
            "This model has variants: choose the one that matches your device."
        )
    release_info = {
        "build_number": submission.build_number,
        "android_version": submission.android_version,
        "changelog": submission.changelog,
    }
    common: dict[str, Any] = {
        "uploader": user,
        "uploaded_brand": model.brand,
        "uploaded_model": model,
        "uploaded_variant": variant,
        "package_format": submission.package_format,
        "extra_info": submission.notes or None,
        "access_tier": submission.access_tier,
        "proposed_price": submission.proposed_price,
        "release_info": release_info,
    }
    if submission.file is not None:
        pending = handle_upload(
            file_obj=submission.file,
            is_password_protected=submission.is_password_protected,
            password=submission.archive_password,
            **common,
        )
        attempt_extraction(pending)
        run_ai_analysis(pending)
        return pending
    if not submission.external_url:
        raise FirmwareUploadError("Attach a file or provide a download link.")
    pending = submit_external_firmware(
        url=submission.external_url,
        original_file_name=file_name_from_url(submission.external_url),
        **common,
    )
    if pending.uploader_id != user.pk:
        raise FirmwareUploadError(
            "This download link was already submitted and is awaiting review."
        )
    return pending
