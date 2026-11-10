"""Change the access type of an approved firmware file, with an audit trail.

``apply_access_change`` is the only writer of ``access_tier`` / ``approved_price`` after
approval. Staff use it from the admin (detail, bulk) and the staff dialog, approved
requests use it (:mod:`apps.firmwares.access_requests`) and the popularity rule uses
it as ``system`` (:mod:`apps.firmwares.auto_feature`). Every call writes one
append-only :class:`AccessChangeLog` row.

What happens to people who already received the file
----------------------------------------------------

Nothing is taken away and nobody is charged retroactively:

* ``ServedDownload`` rows keep the rule they were served under: a free file keeps the
  free re-download allowance of the people who have it (they are grandfathered until
  that allowance ends, then the new access type applies), package files keep their
  package period, and files bought one by one stay theirs for good.
* Purchases are never refunded or revoked by a demotion from paid.
* New downloads follow the new access type at once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from apps.core.events.bus import event_bus

from .access import is_seller
from .access_change_models import (
    AccessActor,
    AccessChangeLog,
    AccessChangeRequest,
    AccessChangeSource,
)
from .models import AccessTier, BaseFirmware
from .notifications import notify_users
from .services import MAX_PRICE

logger = logging.getLogger(__name__)

ACCESS_CHANGED_EVENT = "firmware.access_changed"
TIER_ORDER = (AccessTier.FREE, AccessTier.FEATURED, AccessTier.PAID)


class AccessChangeError(ValueError):
    """A rule refused the access change; the message is safe to show to staff."""


@dataclass(frozen=True)
class ChangeLink:
    """The request an access change fulfils or the change it reverts."""

    request: AccessChangeRequest | None = None
    reverts: AccessChangeLog | None = None


@dataclass(frozen=True)
class AccessChange:
    """What to change and who is changing it (``source`` auto = the system)."""

    to_tier: str
    price: Any = None
    actor: Any = None
    source: str = AccessChangeSource.DIALOG
    reason: str = ""
    notify: bool = True
    link: ChangeLink = ChangeLink()

    @property
    def actor_kind(self) -> str:
        """Who is acting: the system for the popularity rule, else staff."""
        if self.source == AccessChangeSource.AUTO:
            return AccessActor.SYSTEM
        return AccessActor.STAFF


def content_type_of(firmware: BaseFirmware) -> ContentType:
    """Content type of the typed firmware row (never a proxy or the base class)."""
    return ContentType.objects.get_for_model(firmware, for_concrete_model=False)


def parse_price(raw: Any) -> Decimal:
    """A positive USD amount with two decimals, or :class:`AccessChangeError`."""
    try:
        price = Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as error:
        raise AccessChangeError("Enter the price as a number.") from error
    if price <= 0 or price > MAX_PRICE:
        raise AccessChangeError("A paid file needs a price greater than zero.")
    return price


def _uploader_may_sell(firmware: BaseFirmware) -> bool:
    """Paid files pay their seller: only sellers, staff or platform files qualify."""
    uploader = getattr(firmware, "uploader", None)
    if uploader is None:
        return True
    return bool(uploader.is_staff or is_seller(uploader))


def _validate(firmware: BaseFirmware, change: AccessChange) -> Decimal | None:
    """Check every rule of a change and return the price to store."""
    if change.to_tier not in AccessTier.values:
        raise AccessChangeError("Unsupported access type.")
    if not firmware.is_verified:
        raise AccessChangeError("Only approved files can change their access type.")
    has_price = change.price not in (None, "")
    if change.to_tier != AccessTier.PAID:
        if has_price:
            raise AccessChangeError("Only paid files have a price.")
        price = None
    else:
        if not has_price:
            raise AccessChangeError("A paid file needs an approved price.")
        price = parse_price(change.price)
    if firmware.access_tier == change.to_tier and firmware.approved_price == price:
        raise AccessChangeError("The file already has this access type and price.")
    if change.actor_kind == AccessActor.SYSTEM and not (
        firmware.access_tier == AccessTier.FREE
        and change.to_tier == AccessTier.FEATURED
    ):
        raise AccessChangeError("The system only promotes free files to featured.")
    if change.to_tier == AccessTier.PAID and not _uploader_may_sell(firmware):
        raise AccessChangeError(
            "Paid files pay their seller: the uploader is not an approved seller."
        )
    own_file = (
        change.actor is not None
        and change.actor_kind == AccessActor.STAFF
        and firmware.uploader_id == change.actor.pk
    )
    if own_file and change.to_tier != AccessTier.FREE:
        raise AccessChangeError(
            "You cannot make your own file paid or featured; ask another reviewer."
        )
    return price


def _close_pending_requests(
    firmware: BaseFirmware, change: AccessChange, log: AccessChangeLog
) -> None:
    """A direct change makes the other open requests for the file moot."""
    pending = AccessChangeRequest.objects.filter(
        content_type=log.content_type,
        object_id=firmware.pk,
        status=AccessChangeRequest.Status.PENDING,
    )
    if change.link.request is not None:
        pending = pending.exclude(pk=change.link.request.pk)
    pending.update(
        status=AccessChangeRequest.Status.WITHDRAWN,
        review_note="Superseded by a direct change of the access type.",
        reviewed_at=timezone.now(),
    )


def _describe(tier: str, price: Decimal | None) -> str:
    label = str(AccessTier(tier).label)
    return f"{label} (USD {price:.2f})" if price is not None else label


def _announce(firmware: BaseFirmware, log: AccessChangeLog, notify: bool) -> None:
    """After commit: publish the event and tell the uploader."""
    event_bus.publish(
        ACCESS_CHANGED_EVENT,
        {
            "firmware": firmware,
            "firmware_id": str(firmware.pk),
            "from_tier": log.from_tier,
            "to_tier": log.to_tier,
            "price": log.to_price,
            "actor_kind": log.actor_kind,
            "log_id": log.pk,
        },
    )
    uploader_id = getattr(firmware, "uploader_id", None)
    actor_id = log.actor_id
    if not notify or uploader_id is None or uploader_id == actor_id:
        return
    notify_users(
        [uploader_id],
        sender=AccessChangeLog,
        title="Access type of your file changed",
        message=(
            f"{firmware.original_file_name} is now "
            f"{_describe(log.to_tier, log.to_price)}, "
            f"was {_describe(log.from_tier, log.from_price)}."
            + (f" Reason: {log.reason}" if log.reason else "")
            + " People who already downloaded it keep their access."
        ),
        priority="info",
        subject=firmware,
    )


def apply_access_change(
    firmware: BaseFirmware, change: AccessChange
) -> AccessChangeLog:
    """Move ``firmware`` to another access type (and price) and record it.

    Runs in one transaction on the locked row. Raises :class:`AccessChangeError`
    when a rule refuses the change; nothing is written then.
    """
    with transaction.atomic():
        locked = type(firmware).objects.select_for_update().get(pk=firmware.pk)
        price = _validate(locked, change)
        from_tier, from_price = str(locked.access_tier), locked.approved_price
        locked.access_tier = change.to_tier
        locked.approved_price = price
        fields = ["access_tier", "approved_price", "updated_at"]
        if change.actor is not None:
            locked.price_reviewed_by = change.actor
            locked.price_reviewed_at = timezone.now()
            fields += ["price_reviewed_by", "price_reviewed_at"]
        locked.save(update_fields=fields)
        log = AccessChangeLog.objects.create(
            content_type=content_type_of(locked),
            object_id=locked.pk,
            firmware_label=str(locked.original_file_name)[:255],
            actor=change.actor,
            actor_kind=change.actor_kind,
            source=change.source,
            from_tier=from_tier,
            to_tier=change.to_tier,
            from_price=from_price,
            to_price=price,
            reason=change.reason[:1000],
            request=change.link.request,
            reverts=change.link.reverts,
        )
        _close_pending_requests(locked, change, log)
        transaction.on_commit(lambda: _announce(locked, log, change.notify))
    firmware.access_tier = locked.access_tier
    firmware.approved_price = locked.approved_price
    return log


def history_for(firmware: BaseFirmware) -> Any:
    """The change history of one file, newest first."""
    return AccessChangeLog.objects.filter(
        content_type=content_type_of(firmware), object_id=firmware.pk
    )


def manually_demoted(firmware: BaseFirmware) -> bool:
    """Whether a person ever took this file out of featured (never auto-promote it)."""
    return (
        history_for(firmware)
        .filter(from_tier=AccessTier.FEATURED)
        .exclude(to_tier=AccessTier.FEATURED)
        .exclude(actor_kind=AccessActor.SYSTEM)
        .exists()
    )


def revert_change(log: AccessChangeLog, *, actor: Any, reason: str) -> AccessChangeLog:
    """Undo ``log`` by moving the file back to its earlier access type and price.

    Only the file's latest change can be reverted, so history stays linear. The
    revert is a staff change: reverting an automatic promotion therefore also
    blocks the file from being auto-promoted again.
    """
    firmware = log.firmware
    if firmware is None:
        raise AccessChangeError("The file no longer exists.")
    latest = history_for(firmware).first()
    if latest is None or latest.pk != log.pk:
        raise AccessChangeError("Only the latest change of a file can be reverted.")
    return apply_access_change(
        firmware,
        AccessChange(
            to_tier=log.from_tier,
            price=log.from_price,
            actor=actor,
            source=AccessChangeSource.REVERT,
            reason=reason or "Reverted.",
            link=ChangeLink(reverts=log),
        ),
    )
