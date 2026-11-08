"""Promotion lifecycle: draft → scheduled → active → ended, and its announcement.

Activation is time-driven: ``sync_statuses`` (Celery beat every minute) and the
``post_save`` receiver both call :func:`reconcile`, which is idempotent. While a
promotion is active its announcement is published as a site banner for the audience
and notified once; when it ends the announcement expires.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Final

from django.apps import apps as django_apps
from django.db import transaction
from django.utils import timezone
from django.utils.text import Truncator
from kombu.exceptions import OperationalError

from . import gateways
from .audience import audience_users
from .grants import lock_promotion, log, offer_path
from .models import (
    ENTITLEMENT_KINDS,
    Audience,
    GrantOutcome,
    Promotion,
    PromotionGrant,
    PromotionStatus,
)

logger = logging.getLogger(__name__)

NOTIFY_BATCH: Final = 200


class LifecycleError(ValueError):
    """The requested status change is not allowed from the current state."""


def _transition(promotion: Promotion, status: str, **fields: Any) -> None:
    Promotion.objects.filter(pk=promotion.pk).update(status=status, **fields)


# ---------------------------------------------------------------- staff actions


def _validated(promotion: Promotion) -> None:
    promotion.full_clean()


@transaction.atomic
def schedule(promotion: Promotion, *, actor: Any = None) -> Promotion:
    """Draft → scheduled (the status sync starts it when its window opens)."""
    locked = lock_promotion(promotion.pk)
    if locked.status != PromotionStatus.DRAFT:
        raise LifecycleError("Only a draft can be scheduled.")
    _validated(locked)
    now = timezone.now()
    if locked.ends_at and locked.ends_at <= now:
        raise LifecycleError("The end date has already passed.")
    _transition(locked, PromotionStatus.SCHEDULED)
    log(locked, "scheduled", actor=actor)
    return reconcile(locked.pk)


@transaction.atomic
def activate_now(promotion: Promotion, *, actor: Any = None) -> Promotion:
    """Draft or scheduled → active immediately."""
    locked = lock_promotion(promotion.pk)
    if locked.status not in (PromotionStatus.DRAFT, PromotionStatus.SCHEDULED):
        raise LifecycleError("Only a draft or scheduled promotion can be started.")
    _validated(locked)
    now = timezone.now()
    if locked.ends_at and locked.ends_at <= now:
        raise LifecycleError("The end date has already passed.")
    _transition(locked, PromotionStatus.SCHEDULED, starts_at=now)
    log(locked, "started_now", actor=actor)
    return reconcile(locked.pk)


@transaction.atomic
def end_now(promotion: Promotion, *, actor: Any = None) -> Promotion:
    """Scheduled or active → ended; grants without a trial length stop with it."""
    locked = lock_promotion(promotion.pk)
    if locked.status not in (PromotionStatus.SCHEDULED, PromotionStatus.ACTIVE):
        raise LifecycleError("Only a scheduled or running promotion can be ended.")
    now = timezone.now()
    _transition(locked, PromotionStatus.ENDED, ended_at=now)
    if not locked.trial_days and locked.kind in ENTITLEMENT_KINDS:
        PromotionGrant.objects.filter(
            promotion=locked, outcome=GrantOutcome.GRANTED, expires_at__gt=now
        ).update(expires_at=now)
        gateways.cancel_promotion_packages(gateways.package_label(locked.pk))
    log(locked, "ended", actor=actor)
    return reconcile(locked.pk)


@transaction.atomic
def return_to_draft(promotion: Promotion, *, actor: Any = None) -> Promotion:
    """Scheduled → draft, for edits before it starts."""
    locked = lock_promotion(promotion.pk)
    if locked.status != PromotionStatus.SCHEDULED:
        raise LifecycleError("Only a scheduled promotion can go back to draft.")
    _transition(locked, PromotionStatus.DRAFT)
    log(locked, "returned_to_draft", actor=actor)
    return reconcile(locked.pk)


@transaction.atomic
def duplicate(promotion: Promotion, *, actor: Any = None) -> Promotion:
    """A draft copy with a fresh name and slug and no counters or announcement."""
    copy = Promotion.objects.get(pk=promotion.pk)
    copy.pk = None
    copy.announcement = None
    base = f"{promotion.name} (copy)"
    name, slug, number = base, f"{promotion.slug}-copy", 1
    while (
        Promotion.objects.filter(name=name).exists()
        or Promotion.objects.filter(slug=slug).exists()
    ):
        number += 1
        name, slug = f"{base} {number}", f"{promotion.slug}-copy-{number}"
    copy.name, copy.slug = name[:120], slug[:120]
    copy.status = PromotionStatus.DRAFT
    copy.page_views = copy.banner_views = copy.announcement_clicks = 0
    copy.claim_count = copy.conversion_count = copy.credits_granted = 0
    copy.notify_cursor = 0
    copy.notified_at = copy.activated_at = copy.ended_at = None
    copy.created_by = actor if getattr(actor, "is_authenticated", False) else None
    copy.save()
    log(copy, "duplicated", actor=actor, detail=f"from {promotion.slug}")
    return copy


# ---------------------------------------------------------------- reconcile


def _next_status(promotion: Promotion, now: datetime) -> str:
    status = promotion.status
    if (
        status == PromotionStatus.SCHEDULED
        and (promotion.starts_at is None or promotion.starts_at <= now)
        and not (promotion.ends_at and promotion.ends_at <= now)
    ):
        status = PromotionStatus.ACTIVE
    if status in (
        PromotionStatus.SCHEDULED,
        PromotionStatus.ACTIVE,
    ) and (promotion.ends_at and promotion.ends_at <= now):
        status = PromotionStatus.ENDED
    return status


@transaction.atomic
def reconcile(promotion_id: int, *, now: datetime | None = None) -> Promotion:
    """Apply time-driven status changes and keep the announcement in step."""
    moment = now or timezone.now()
    promotion = lock_promotion(promotion_id)
    target = _next_status(promotion, moment)
    if target != promotion.status:
        fields: dict[str, Any] = {}
        if target == PromotionStatus.ACTIVE:
            fields["activated_at"] = moment
        if target == PromotionStatus.ENDED:
            fields["ended_at"] = moment
        _transition(promotion, target, **fields)
        log(promotion, f"became_{target}", detail="time-driven")
        promotion.refresh_from_db()
    sync_announcement(promotion)
    if (
        promotion.status == PromotionStatus.ACTIVE
        and promotion.announce
        and promotion.notified_at is None
    ):
        dispatch_notifications(promotion.pk)
    return promotion


def sync_statuses(*, now: datetime | None = None) -> dict[str, int]:
    """Advance every scheduled or active promotion and finish pending notifications."""
    activated = ended = notified = 0
    open_ids = Promotion.objects.filter(
        status__in=(PromotionStatus.SCHEDULED, PromotionStatus.ACTIVE)
    ).values_list("pk", "status")
    for promotion_id, before in list(open_ids):
        after = reconcile(promotion_id, now=now).status
        activated += (
            before != PromotionStatus.ACTIVE and after == PromotionStatus.ACTIVE
        )
        ended += after == PromotionStatus.ENDED
    pending = Promotion.objects.filter(
        status=PromotionStatus.ACTIVE, announce=True, notified_at__isnull=True
    ).values_list("pk", flat=True)
    for promotion_id in list(pending):
        notified += notify_audience(promotion_id)
    return {"activated": activated, "ended": ended, "notified": notified}


# ---------------------------------------------------------------- announcement


def _announcement_message(promotion: Promotion) -> str:
    text = promotion.effect_summary()
    if promotion.description:
        text = f"{text}. {Truncator(promotion.description).chars(140)}"
    if promotion.ends_at:
        text = f"{text} Ends {promotion.ends_at:%d %b %Y}."
    return text


def sync_announcement(promotion: Promotion) -> None:
    """Publish the announcement while active, expire it otherwise (idempotent)."""
    announcement_model = django_apps.get_model("users", "Announcement")
    now = timezone.now()
    publish = promotion.announce and promotion.status == PromotionStatus.ACTIVE
    if not publish:
        if promotion.announcement_id is not None:
            expires = (
                {"expires_at": now} if promotion.status == PromotionStatus.ENDED else {}
            )
            announcement_model.objects.filter(pk=promotion.announcement_id).update(
                is_active=False, **expires
            )
        return
    fields = {
        "title": (promotion.headline or promotion.name)[:255],
        "message": _announcement_message(promotion),
        "audience": "all" if promotion.audience == Audience.EVERYONE else "user",
        "is_active": True,
        "is_global": True,
        "start_at": promotion.starts_at or promotion.activated_at or now,
        "expires_at": promotion.ends_at,
        "cta_label": promotion.cta_label,
        "cta_url": offer_path(promotion, "banner"),
    }
    if promotion.announcement_id is None:
        announcement = announcement_model.objects.create(
            created_by=promotion.created_by, **fields
        )
        Promotion.objects.filter(pk=promotion.pk).update(announcement=announcement)
        promotion.announcement = announcement
        log(promotion, "announcement_published", detail=str(announcement.pk))
    else:
        announcement_model.objects.filter(pk=promotion.announcement_id).update(**fields)


# ---------------------------------------------------------------- notifications


def dispatch_notifications(promotion_id: int) -> None:
    """Queue the one-off audience notification after the surrounding commit."""

    def enqueue() -> None:
        from .tasks import notify_audience_task

        try:
            notify_audience_task.delay(promotion_id)
        except OperationalError:
            logger.warning(
                "Broker unavailable; the status sync will notify promotion %s.",
                promotion_id,
            )

    transaction.on_commit(enqueue)


def notify_audience(promotion_id: int, *, batch: int = NOTIFY_BATCH) -> int:
    """Notify the audience once, in resumable batches; returns users notified."""
    sent = 0
    while True:
        with transaction.atomic():
            promotion = lock_promotion(promotion_id)
            if (
                promotion.status != PromotionStatus.ACTIVE
                or not promotion.announce
                or promotion.notified_at is not None
            ):
                return sent
            users = list(
                audience_users(promotion).filter(pk__gt=promotion.notify_cursor)[:batch]
            )
            if not users:
                _transition(promotion, promotion.status, notified_at=timezone.now())
                log(promotion, "audience_notified", detail=f"{promotion.notify_cursor}")
                return sent
            gateways.notify_many(
                users,
                promotion.headline or promotion.name,
                promotion.effect_summary(),
                offer_path(promotion, "notification"),
            )
            Promotion.objects.filter(pk=promotion.pk).update(notify_cursor=users[-1].pk)
            sent += len(users)
