"""Requests to change a file's access type: the uploader asks, staff decide.

Normal users never change an access type themselves. The uploader of an approved
file (an approved seller for paid and featured files) files an
:class:`AccessChangeRequest` with a reason and, for paid files, a price proposal.
Staff approve it (optionally with another approved price) or reject it with a note;
either decision notifies the requester and approval is recorded in the append-only
change history like any other change.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .access import is_seller
from .access_change_models import (
    AccessChangeRequest,
    AccessChangeSource,
)
from .access_changes import (
    AccessChange,
    AccessChangeError,
    ChangeLink,
    apply_access_change,
    content_type_of,
    parse_price,
)
from .models import AccessTier, BaseFirmware
from .notifications import notify_users

REASON_MIN_LENGTH = 10
MAX_REVIEWERS_NOTIFIED = 50
REVIEW_PERMISSION = "change_accesschangerequest"


def _reviewer_ids() -> list[int]:
    """Active staff who may decide requests (superusers and permission holders)."""
    permission = Permission.objects.filter(
        codename=REVIEW_PERMISSION, content_type__app_label="firmwares"
    ).first()
    holders = Q(is_superuser=True)
    if permission is not None:
        holders |= Q(user_permissions=permission) | Q(groups__permissions=permission)
    users = get_user_model().objects.filter(holders, is_active=True, is_staff=True)
    return list(users.values_list("pk", flat=True).distinct()[:MAX_REVIEWERS_NOTIFIED])


def _notify_reviewers(access_request: AccessChangeRequest) -> None:
    text = (
        f"{access_request.firmware_label}: {access_request.from_tier} → "
        f"{access_request.to_tier}. Review it under Access change requests."
    )
    transaction.on_commit(
        lambda: notify_users(
            _reviewer_ids(),
            sender=AccessChangeRequest,
            title="New access change request",
            message=text,
            priority="info",
            subject=access_request,
        )
    )


def _notify_requester(
    access_request: AccessChangeRequest, title: str, text: str
) -> None:
    if access_request.requester_id is None:
        return
    requester_id = access_request.requester_id
    transaction.on_commit(
        lambda: notify_users(
            [requester_id],
            sender=AccessChangeRequest,
            title=title,
            message=text,
            priority="info",
            subject=access_request,
        )
    )


def can_request(user: Any, firmware: BaseFirmware) -> bool:
    """Whether ``user`` is the uploader of an approved file and may ask for a change."""
    return bool(
        user is not None
        and user.is_authenticated
        and firmware.is_verified
        and firmware.uploader_id == user.pk
    )


def pending_request(firmware: BaseFirmware) -> AccessChangeRequest | None:
    """The open request for this file, if any."""
    return AccessChangeRequest.objects.filter(
        content_type=content_type_of(firmware),
        object_id=firmware.pk,
        status=AccessChangeRequest.Status.PENDING,
    ).first()


def submit_request(
    firmware: BaseFirmware,
    requester: Any,
    *,
    to_tier: str,
    proposed_price: Any = None,
    reason: str,
) -> AccessChangeRequest:
    """File a request to move ``firmware`` to ``to_tier`` (and price) for staff."""
    if not can_request(requester, firmware):
        raise AccessChangeError("Only the uploader of an approved file can request.")
    if to_tier not in AccessTier.values or to_tier == firmware.access_tier:
        raise AccessChangeError("Choose a different access type.")
    if to_tier != AccessTier.FREE and not is_seller(requester):
        raise AccessChangeError(
            "Only approved sellers can ask for paid or featured access."
        )
    has_price = proposed_price not in (None, "")
    if to_tier == AccessTier.PAID:
        price = parse_price(proposed_price) if has_price else None
        if price is None:
            raise AccessChangeError("Propose a price for a paid file.")
    elif has_price:
        raise AccessChangeError("Only paid files have a price.")
    else:
        price = None
    text = reason.strip()
    if len(text) < REASON_MIN_LENGTH:
        raise AccessChangeError("Explain why in at least a few words.")
    try:
        with transaction.atomic():
            created = AccessChangeRequest.objects.create(
                content_type=content_type_of(firmware),
                object_id=firmware.pk,
                firmware_label=str(firmware.original_file_name)[:255],
                requester=requester,
                from_tier=firmware.access_tier,
                to_tier=to_tier,
                proposed_price=price,
                reason=text[:1000],
            )
            _notify_reviewers(created)
    except IntegrityError as error:
        raise AccessChangeError("This file already has a pending request.") from error
    return created


def _decidable(access_request: AccessChangeRequest, staff: Any) -> AccessChangeRequest:
    locked = AccessChangeRequest.objects.select_for_update().get(pk=access_request.pk)
    if not locked.is_pending:
        raise AccessChangeError("This request has already been decided.")
    if locked.requester_id == staff.pk:
        raise AccessChangeError("You cannot decide your own request.")
    return locked


@transaction.atomic
def approve_request(
    access_request: AccessChangeRequest,
    staff: Any,
    *,
    approved_price: Any = None,
    note: str = "",
) -> AccessChangeRequest:
    """Apply the request (with staff's price for paid files); notify the requester."""
    locked = _decidable(access_request, staff)
    firmware = locked.firmware
    if firmware is None:
        raise AccessChangeError("The file no longer exists.")
    if firmware.access_tier != locked.from_tier:
        raise AccessChangeError(
            "The file's access type changed since this request; reject it and ask "
            "for a new one."
        )
    price = None
    if locked.to_tier == AccessTier.PAID:
        given = approved_price not in (None, "")
        price = approved_price if given else locked.proposed_price
    apply_access_change(
        firmware,
        AccessChange(
            to_tier=locked.to_tier,
            price=price,
            actor=staff,
            source=AccessChangeSource.REQUEST,
            reason=note or locked.reason,
            notify=False,
            link=ChangeLink(request=locked),
        ),
    )
    locked.status = AccessChangeRequest.Status.APPROVED
    locked.approved_price = firmware.approved_price
    locked.reviewed_by = staff
    locked.reviewed_at = timezone.now()
    locked.review_note = note[:1000]
    locked.save()
    _notify_requester(
        locked,
        "Access change approved",
        f"{locked.firmware_label} is now {AccessTier(locked.to_tier).label}"
        + (f" (USD {locked.approved_price:.2f})" if locked.approved_price else "")
        + ". People who already downloaded it keep their access.",
    )
    return locked


@transaction.atomic
def reject_request(
    access_request: AccessChangeRequest,
    staff: Any,
    *,
    note: str,
) -> AccessChangeRequest:
    """Reject the request; ``note`` is required and sent to the requester."""
    if not note.strip():
        raise AccessChangeError("Give the requester a reason for the rejection.")
    locked = _decidable(access_request, staff)
    locked.status = AccessChangeRequest.Status.REJECTED
    locked.reviewed_by = staff
    locked.reviewed_at = timezone.now()
    locked.review_note = note.strip()[:1000]
    locked.save()
    _notify_requester(
        locked,
        "Access change not approved",
        f"Your request for {locked.firmware_label} was not approved. "
        f"Reason: {locked.review_note}",
    )
    return locked


@transaction.atomic
def withdraw_request(
    access_request: AccessChangeRequest, user: Any
) -> AccessChangeRequest:
    """The requester takes a pending request back."""
    locked = AccessChangeRequest.objects.select_for_update().get(pk=access_request.pk)
    if locked.requester_id != user.pk or not locked.is_pending:
        raise AccessChangeError("This request cannot be withdrawn.")
    locked.status = AccessChangeRequest.Status.WITHDRAWN
    locked.reviewed_at = timezone.now()
    locked.save()
    return locked
