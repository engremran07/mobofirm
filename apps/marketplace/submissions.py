"""Read-only views of a seller's firmware uploads, owned by ``apps.firmwares``.

The access tier and price fields (``access_tier``, ``proposed_price``,
``approved_price``) belong to firmwares (``TieredAccess``); this module only
reads them.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final

from django.db.models import Count
from django.urls import reverse

from apps.firmwares.catalog import (
    FIRMWARE_TYPE_DISPLAY,
    PUBLIC_FIRMWARE_TYPES,
    TYPE_KEY_BY_CLASS,
    get_firmware_model,
    get_public_firmware_queryset,
)
from apps.firmwares.models import AccessTier, BaseFirmware, PendingFirmware

if TYPE_CHECKING:
    from apps.users.models import CustomUser

SUBMISSION_LIMIT: Final[int] = 25
PUBLIC_FILE_LIMIT: Final[int] = 12
DECISION_LABELS: Final[dict[str, str]] = {
    "pending": "Waiting for review",
    "approved": "Approved",
    "rejected": "Rejected",
}
DECISION_TONES: Final[dict[str, str]] = {
    "pending": "warning",
    "approved": "success",
    "rejected": "danger",
}


@dataclass(frozen=True)
class SubmissionRow:
    pk: str
    file_name: str
    variant_label: str
    tier: str
    tier_label: str
    proposed_price: Decimal | None
    approved_price: Decimal | None
    price_note: str
    status: str
    status_label: str
    status_tone: str
    reason: str
    submitted_at: datetime
    public_url: str


@dataclass(frozen=True)
class SubmissionSummary:
    total: int
    pending: int
    approved: int
    rejected: int


@dataclass(frozen=True)
class PublicFile:
    title: str
    file_name: str
    category: str
    tier_label: str
    url: str
    created_at: datetime


def _variant_label(pending: PendingFirmware) -> str:
    variant = pending.uploaded_variant
    if variant is not None:
        return f"{variant.model.brand.name} {variant.model.name} · {variant.name}"
    model = pending.uploaded_model
    if model is not None:
        return f"{model.brand.name} {model.name}"
    return "Not linked to a variant"


def _price_note(tier: str, approved: Decimal | None, status: str) -> str:
    if tier == AccessTier.PAID:
        if approved is None:
            return "Waiting for staff to approve the price"
        return "Price approved by staff"
    if tier == AccessTier.FEATURED:
        return "Downloadable by package holders only"
    if status == "rejected":
        return ""
    return "Free to download"


def _public_urls(rows: list[PendingFirmware]) -> dict[Any, str]:
    """Detail URLs for approved uploads whose typed record is public."""
    wanted: dict[type[BaseFirmware], list[Any]] = defaultdict(list)
    for pending in rows:
        if pending.admin_decision != "approved" or pending.routed_object_id is None:
            continue
        model_class = get_firmware_model(pending.routed_category)
        if model_class is None:
            continue
        wanted[model_class].append(pending.routed_object_id)
    urls: dict[Any, str] = {}
    for firmware_class, ids in wanted.items():
        public_ids = get_public_firmware_queryset(firmware_class).filter(pk__in=ids)
        for pk in public_ids.values_list("pk", flat=True):
            urls[pk] = reverse(
                "firmwares:firmware_detail",
                kwargs={
                    "firmware_type": TYPE_KEY_BY_CLASS[firmware_class],
                    "firmware_id": pk,
                },
            )
    return urls


def submissions_for(
    user: CustomUser, limit: int = SUBMISSION_LIMIT
) -> list[SubmissionRow]:
    """The user's own uploads, newest first (never other users' uploads)."""
    pending_rows = list(
        PendingFirmware.objects.filter(uploader=user)
        .select_related("uploaded_model__brand", "uploaded_variant__model__brand")
        .order_by("-created_at")[:limit]
    )
    urls = _public_urls(pending_rows)
    rows: list[SubmissionRow] = []
    for pending in pending_rows:
        tier = str(pending.access_tier)
        rows.append(
            SubmissionRow(
                pk=str(pending.pk),
                file_name=pending.original_file_name,
                variant_label=_variant_label(pending),
                tier=tier,
                tier_label=AccessTier(tier).label,
                proposed_price=pending.proposed_price,
                approved_price=pending.approved_price,
                price_note=_price_note(
                    tier, pending.approved_price, pending.admin_decision
                ),
                status=pending.admin_decision,
                status_label=DECISION_LABELS.get(
                    pending.admin_decision, pending.admin_decision.title()
                ),
                status_tone=DECISION_TONES.get(pending.admin_decision, ""),
                reason=pending.admin_notes
                if pending.admin_decision == "rejected"
                else "",
                submitted_at=pending.created_at,
                public_url=urls.get(pending.routed_object_id, ""),
            )
        )
    return rows


def submission_summary(user: CustomUser) -> SubmissionSummary:
    counts = dict(
        PendingFirmware.objects.filter(uploader=user)
        .order_by()
        .values_list("admin_decision")
        .annotate(total=Count("pk"))
    )
    pending = counts.get("pending", 0)
    approved = counts.get("approved", 0)
    rejected = counts.get("rejected", 0)
    return SubmissionSummary(
        total=sum(counts.values()),
        pending=pending,
        approved=approved,
        rejected=rejected,
    )


def public_files_for(
    user: CustomUser, limit: int = PUBLIC_FILE_LIMIT
) -> list[PublicFile]:
    """Approved, public firmware the user uploaded, newest first."""
    files: list[PublicFile] = []
    for key, model_class in PUBLIC_FIRMWARE_TYPES:
        queryset = (
            get_public_firmware_queryset(model_class)
            .filter(uploader=user)
            .select_related("brand", "model", "variant")
            .order_by("-created_at")[:limit]
        )
        for firmware in queryset:
            title = " ".join(
                part
                for part in (
                    firmware.brand.name if firmware.brand else "",
                    firmware.model.name if firmware.model else "",
                    firmware.variant.name if firmware.variant else "",
                )
                if part
            )
            files.append(
                PublicFile(
                    title=title or firmware.original_file_name,
                    file_name=firmware.original_file_name,
                    category=FIRMWARE_TYPE_DISPLAY[key],
                    tier_label=AccessTier(firmware.access_tier).label,
                    url=reverse(
                        "firmwares:firmware_detail",
                        kwargs={"firmware_type": key, "firmware_id": firmware.pk},
                    ),
                    created_at=firmware.created_at,
                )
            )
    files.sort(key=lambda item: item.created_at, reverse=True)
    return files[:limit]
