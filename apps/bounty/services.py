"""Bounty lifecycle: post (escrow), claim, settle, cancel, expire, dispute.

Credits move only through the wallet contract (``apps.wallet.api``): the poster's
credits are locked in escrow when the bounty is posted, paid to the uploader when
staff approve a matching upload, and returned on cancellation or expiry. Every state
change is atomic, idempotent and written to ``BountyLog``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Final

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, QuerySet, Sum
from django.utils import timezone

from apps.bounty import targets
from apps.bounty.models import Bounty, BountyClaim, BountyDispute, BountyLog
from apps.core.app_service import app_enabled
from apps.core.events.bus import event_bus

logger = logging.getLogger(__name__)

MIN_CREDITS: Final = 10
MAX_CREDITS: Final = 100_000
DEFAULT_DAYS: Final = 30
MAX_DAYS: Final = 90
MAX_OPEN_PER_USER: Final = 10

BOUNTY_POSTED: Final = "bounty.posted"
BOUNTY_SETTLED: Final = "bounty.settled"
BOUNTY_CLOSED: Final = "bounty.closed"


class BountyError(ValueError):
    """A business rule refused the action; the message is safe to show."""


class BountyUnavailable(BountyError):
    """Bounties are paused (wallet missing or the app is switched off)."""


class NotEnoughCredits(BountyError):
    def __init__(self, needed: int, available: int) -> None:
        super().__init__(
            f"You need {needed} credits but have {available} available to escrow."
        )
        self.needed = needed
        self.available = available


def availability(kind: str | None = targets.KIND_REQUEST) -> tuple[bool, str]:
    """Whether bounties can be used, and an honest reason when they cannot.

    ``kind`` names the target being asked about; ``None`` checks only the escrow
    prerequisites (settling or refunding an existing bounty needs no catalogue).
    """
    if not apps.is_installed("apps.wallet"):
        return False, "Bounties are paused: the credit wallet is not enabled."
    if not app_enabled("wallet"):
        return False, "Bounties are paused: credits are switched off."
    if not app_enabled("bounty"):
        return False, "Bounties are switched off by the site operator."
    if kind in targets.CATALOGUE_KINDS and not targets.catalogue_installed():
        return False, "Bounties are paused: the firmware catalogue is not enabled."
    if kind == targets.KIND_GUIDE and not (
        apps.is_installed("apps.blog") and app_enabled("blog")
    ):
        return False, "Bounties on guide requests are paused: the blog is not enabled."
    return True, ""


def _wallet(kind: str | None = None) -> Any:
    ok, reason = availability(kind)
    if not ok:
        raise BountyUnavailable(reason)
    from apps.wallet import api

    return api


def _hold(bounty: Bounty) -> Any:
    if bounty.escrow_hold_id is None:
        raise BountyError("This bounty has no escrow.")
    hold_model = apps.get_model("wallet", "EscrowHold")
    return hold_model.objects.get(pk=bounty.escrow_hold_id)


def _log(bounty: Bounty, kind: str, actor: Any = None, detail: str = "") -> None:
    BountyLog.objects.create(
        bounty=bounty,
        kind=kind,
        actor=actor if getattr(actor, "pk", None) else None,
        detail=detail[:500],
    )


def _notify(user_id: int | None, title: str, message: str) -> None:
    if user_id is None:
        return
    from apps.core.services.notifications import notification_service

    notification_service.send(user_id, title, message, "info")


def _publish(event: str, payload: Mapping[str, Any]) -> None:
    transaction.on_commit(lambda: event_bus.publish(event, dict(payload)))


# ------------------------------------------------------------------ posting ----


def _verified(user: Any) -> bool:
    return getattr(user, "email_verified_at", None) is not None


def post_bounty(
    user: Any,
    *,
    kind: str,
    object_id: int,
    amount: int,
    days: int = DEFAULT_DAYS,
    note: str = "",
) -> Bounty:
    """Escrow ``amount`` credits from ``user`` against a request, model, variant or guide request."""
    api = _wallet(kind)
    if not _verified(user):
        raise BountyError("Verify your email address before posting a bounty.")
    if isinstance(amount, bool) or not MIN_CREDITS <= amount <= MAX_CREDITS:
        raise BountyError(
            f"A bounty must be between {MIN_CREDITS} and {MAX_CREDITS} credits."
        )
    if not 1 <= days <= MAX_DAYS:
        raise BountyError(f"A bounty can stay open for 1 to {MAX_DAYS} days.")
    try:
        target = targets.resolve(kind, object_id)
    except targets.TargetError as error:
        raise BountyError(str(error)) from error
    with transaction.atomic():
        open_count = Bounty.objects.filter(
            creator=user, status__in=Bounty.UNSETTLED
        ).count()
        if open_count >= MAX_OPEN_PER_USER:
            raise BountyError(
                f"You can have at most {MAX_OPEN_PER_USER} open bounties at a time."
            )
        now = timezone.now()
        bounty = Bounty.objects.create(
            creator=user,
            title=f"Bounty for {target.label}"[:200],
            note=note.strip()[:1000],
            target_type=target.content_type,
            target_id=target.object_id,
            target_label=target.label,
            credits=amount,
            expires_at=now + timedelta(days=days),
            created_at=now,
        )
        try:
            hold = api.lock_escrow(
                user,
                amount,
                key=bounty.escrow_key,
                reason=f"Bounty #{bounty.pk}",
                reference=f"bounty:{bounty.pk}",
            )
        except api.InsufficientCredits as error:
            raise NotEnoughCredits(error.needed, error.available) from error
        except api.WalletFrozen as error:
            raise BountyError("Your wallet is frozen; contact support.") from error
        bounty.escrow_hold_id = hold.pk
        bounty.save(update_fields=["escrow_hold_id", "updated_at"])
        _log(bounty, "posted", user, f"{amount} credits escrowed")
        _publish(
            BOUNTY_POSTED,
            {"bounty_id": bounty.pk, "credits": amount, "target": target.label},
        )
    return bounty


def claim(bounty: Bounty, user: Any, note: str = "") -> BountyClaim:
    """Record that ``user`` is working on the bounty (idempotent)."""
    if bounty.status != Bounty.Status.OPEN:
        raise BountyError("This bounty is no longer open.")
    if bounty.creator_id == user.pk:
        raise BountyError("You cannot claim your own bounty.")
    if not _verified(user):
        raise BountyError("Verify your email address first.")
    record, created = BountyClaim.objects.get_or_create(
        bounty=bounty, user=user, defaults={"note": note.strip()[:300]}
    )
    if created:
        _log(bounty, "claimed", user)
    return record


# ------------------------------------------------------------- close / refund ----


def _close_unsettled(
    bounty_id: int, *, status: str, actor: Any, reason: str, kind: str
) -> Bounty:
    """Return the escrow to the poster and end the bounty. Idempotent."""
    api = _wallet()
    with transaction.atomic():
        bounty = Bounty.objects.select_for_update().get(pk=bounty_id)
        if bounty.status not in Bounty.UNSETTLED:
            return bounty
        api.cancel_escrow(_hold(bounty))
        bounty.status = status
        bounty.closed_reason = reason[:255]
        bounty.review_reason = ""
        bounty.save(
            update_fields=["status", "closed_reason", "review_reason", "updated_at"]
        )
        _log(bounty, kind, actor, reason)
        _publish(
            BOUNTY_CLOSED,
            {"bounty_id": bounty.pk, "status": status, "credits": bounty.credits},
        )
    _notify(
        bounty.creator_id,
        "Your bounty ended",
        f"{bounty.credits} credits for {bounty.target_label} were returned: {reason}",
    )
    return bounty


def cancel(bounty: Bounty, *, actor: Any, reason: str = "Cancelled") -> Bounty:
    """Poster (or staff) cancels an unsettled bounty; credits return in full."""
    if actor.pk != bounty.creator_id and not actor.has_perm("bounty.change_bounty"):
        raise BountyError("Only the poster or staff can cancel a bounty.")
    if bounty.status not in Bounty.UNSETTLED:
        raise BountyError("Only an unsettled bounty can be cancelled.")
    return _close_unsettled(
        bounty.pk,
        status=Bounty.Status.CANCELLED,
        actor=actor,
        reason=reason or "Cancelled",
        kind="cancelled",
    )


def expire_due(now: Any = None) -> int:
    """Refund and expire every open bounty past its deadline; returns how many."""
    moment = now or timezone.now()
    due = list(
        Bounty.objects.filter(
            status=Bounty.Status.OPEN, expires_at__lte=moment
        ).values_list("pk", flat=True)
    )
    done = 0
    for bounty_id in due:
        try:
            result = _close_unsettled(
                bounty_id,
                status=Bounty.Status.EXPIRED,
                actor=None,
                reason="The bounty expired",
                kind="expired",
            )
        except BountyError:
            logger.exception("Bounty %s could not be expired", bounty_id)
            continue
        done += result.status == Bounty.Status.EXPIRED
    return done


# --------------------------------------------------------------- settlement ----


def settle(bounty_id: int, payee: Any, *, firmware_ref: str = "") -> Bounty:
    """Pay the escrow to ``payee`` for fulfilling the bounty. Idempotent.

    The poster fulfilling their own bounty simply gets the credits back. When the
    wallet refuses the reward (unverified payee, caps, frozen wallet) the bounty
    moves to staff review with the reason instead of failing silently.
    """
    api = _wallet()
    with transaction.atomic():
        bounty = Bounty.objects.select_for_update().get(pk=bounty_id)
        if bounty.status != Bounty.Status.OPEN:
            return bounty
        if payee.pk != bounty.creator_id:
            try:
                api.release_escrow(
                    _hold(bounty),
                    to_user=payee,
                    key=f"bounty:{bounty.pk}:payout",
                    reference=f"bounty:{bounty.pk}",
                )
            except api.WalletError as error:
                bounty.status = Bounty.Status.REVIEW
                bounty.review_reason = f"Payout to {payee} refused: {error}"[:255]
                bounty.save(update_fields=["status", "review_reason", "updated_at"])
                _log(bounty, "review", payee, bounty.review_reason)
                return bounty
            bounty.status = Bounty.Status.FULFILLED
            bounty.settled_at = timezone.now()
            bounty.settled_to = payee
            bounty.settled_firmware = firmware_ref[:100]
            bounty.save(
                update_fields=[
                    "status",
                    "settled_at",
                    "settled_to",
                    "settled_firmware",
                    "updated_at",
                ]
            )
            _log(
                bounty,
                "settled",
                payee,
                f"{bounty.credits} credits paid; {firmware_ref}",
            )
            _publish(
                BOUNTY_SETTLED,
                {
                    "bounty_id": bounty.pk,
                    "credits": bounty.credits,
                    "payee_id": payee.pk,
                    "firmware": firmware_ref,
                },
            )
            paid = True
        else:
            paid = False
    if not paid:
        return _close_unsettled(
            bounty_id,
            status=Bounty.Status.CANCELLED,
            actor=None,
            reason="You fulfilled your own bounty, so the credits were returned",
            kind="self_fulfilled",
        )
    _notify(
        bounty.creator_id,
        "Your bounty was fulfilled",
        f"{payee} fulfilled {bounty.target_label}; {bounty.credits} credits were paid.",
    )
    return bounty


def matching_bounties(payload: Mapping[str, Any]) -> QuerySet[Bounty]:
    """Open bounties a ``firmware.approved`` payload fulfils (request/model/variant)."""
    clauses = Q()
    for kind, ids in (
        (targets.KIND_REQUEST, list(payload.get("fulfilled_request_ids") or ())),
        (targets.KIND_MODEL, [payload.get("model_id")]),
        (targets.KIND_VARIANT, [payload.get("variant_id")]),
    ):
        wanted = [int(value) for value in ids if value]
        if wanted:
            clauses |= Q(
                target_type=targets.content_type_for(kind), target_id__in=wanted
            )
    if not clauses:
        return Bounty.objects.none()
    return Bounty.objects.filter(clauses, status=Bounty.Status.OPEN)


def handle_firmware_approved(payload: Mapping[str, Any]) -> list[Bounty]:
    """Settle every open bounty the approved upload fulfils."""
    uploader = payload.get("uploader")
    if uploader is None or availability()[0] is False:
        return []
    uploaded_at = getattr(payload.get("pending"), "created_at", None)
    firmware_ref = f"{payload.get('firmware_type')}:{payload.get('firmware_id')}"
    settled: list[Bounty] = []
    for bounty in matching_bounties(payload):
        if uploaded_at is not None and uploaded_at < bounty.created_at:
            continue  # uploaded before the bounty existed: not what it asked for
        try:
            settled.append(settle(bounty.pk, uploader, firmware_ref=firmware_ref))
        except BountyError:
            logger.exception("Bounty %s could not be settled", bounty.pk)
    return settled


def handle_guide_fulfilled(payload: Mapping[str, Any]) -> list[Bounty]:
    """``blog.guide_fulfilled``: pay every open bounty on the answered request."""
    author = payload.get("author")
    request_id = payload.get("request_id")
    if author is None or not request_id or availability(None)[0] is False:
        return []
    try:
        content_type = targets.content_type_for(targets.KIND_GUIDE)
    except targets.TargetError:
        return []
    reference = f"guide:{request_id}:post:{payload.get('post_id')}"
    settled: list[Bounty] = []
    for bounty in Bounty.objects.filter(
        target_type=content_type,
        target_id=int(request_id),
        status=Bounty.Status.OPEN,
    ):
        try:
            settled.append(settle(bounty.pk, author, firmware_ref=reference))
        except BountyError:
            logger.exception("Bounty %s could not be settled", bounty.pk)
    return settled


def handle_guide_request_closed(payload: Mapping[str, Any]) -> list[Bounty]:
    """``blog.guide_request_closed``: refund bounties on a rejected or cancelled request."""
    request_id = payload.get("request_id")
    if not request_id or availability(None)[0] is False:
        return []
    try:
        content_type = targets.content_type_for(targets.KIND_GUIDE)
    except targets.TargetError:
        return []
    closed: list[Bounty] = []
    for bounty in Bounty.objects.filter(
        target_type=content_type,
        target_id=int(request_id),
        status__in=Bounty.UNSETTLED,
    ):
        try:
            closed.append(
                _close_unsettled(
                    bounty.pk,
                    status=Bounty.Status.CANCELLED,
                    actor=None,
                    reason="The guide request was closed",
                    kind="target_closed",
                )
            )
        except BountyError:
            logger.exception("Bounty %s could not be refunded", bounty.pk)
    return closed


# ----------------------------------------------------------------- disputes ----


def report_problem(bounty: Bounty, user: Any, reason: str) -> BountyDispute:
    if bounty.status not in Bounty.UNSETTLED:
        raise BountyError("Problems can only be reported on an unsettled bounty.")
    text = reason.strip()
    if len(text) < 10:
        raise BountyError("Describe the problem in at least 10 characters.")
    try:
        with transaction.atomic():
            dispute = BountyDispute.objects.create(
                bounty=bounty, reporter=user, reason=text[:1000]
            )
    except IntegrityError as error:
        raise BountyError("You already have an open report on this bounty.") from error
    _log(bounty, "reported", user, text[:200])
    return dispute


def resolve_dispute(
    dispute: BountyDispute,
    *,
    outcome: str,
    staff: Any,
    note: str = "",
    payee: Any = None,
) -> BountyDispute:
    """Staff decision: refund the poster, pay ``payee``, or dismiss the report."""
    if outcome not in BountyDispute.Outcome.values:
        raise BountyError("Unknown outcome.")
    with transaction.atomic():
        locked = BountyDispute.objects.select_for_update().get(pk=dispute.pk)
        if locked.status == BountyDispute.Status.RESOLVED:
            return locked
        bounty = locked.bounty
        if outcome == BountyDispute.Outcome.REFUNDED:
            _close_unsettled(
                bounty.pk,
                status=Bounty.Status.CANCELLED,
                actor=staff,
                reason=note or "Refunded after a staff review",
                kind="refunded",
            )
        elif outcome == BountyDispute.Outcome.PAID:
            if payee is None:
                raise BountyError("Choose who is paid.")
            if bounty.status == Bounty.Status.REVIEW:
                Bounty.objects.filter(pk=bounty.pk).update(status=Bounty.Status.OPEN)
            result = settle(bounty.pk, payee, firmware_ref="staff decision")
            if result.status != Bounty.Status.FULFILLED:
                raise BountyError(result.review_reason or "The payout was refused.")
            locked.payee = payee
        locked.status = BountyDispute.Status.RESOLVED
        locked.outcome = outcome
        locked.resolution_note = note[:500]
        locked.resolved_by = staff
        locked.resolved_at = timezone.now()
        locked.save()
        _log(bounty, "dispute_resolved", staff, f"{outcome}: {note}"[:500])
    _notify(
        locked.reporter_id,
        "Your bounty report was reviewed",
        f"Outcome: {locked.get_outcome_display()}.",
    )
    return locked


# -------------------------------------------------------------------- stats ----


def open_credits_for(kind: str, object_id: int) -> tuple[int, int]:
    """``(bounty count, total credits)`` currently open on one target."""
    totals = Bounty.objects.filter(
        target_type=targets.content_type_for(kind),
        target_id=object_id,
        status=Bounty.Status.OPEN,
    ).aggregate(total=Sum("credits"), count=Count("id"))
    return int(totals["count"] or 0), int(totals["total"] or 0)
