"""Referral business logic: capture a code, link the invitee, reward once they qualify.

A referrer is rewarded (wallet event ``referral.verified``, key ``referral:<invitee>``)
only after the invitee verified their email AND, unless staff relax it, had a first
contribution approved. One reward per invitee is guaranteed by the one-to-one
``Referral.invitee`` link plus the wallet's idempotent key.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.app_service import AppService
from apps.core.events.bus import event_bus
from apps.core.utils.ip import get_client_ip
from apps.wallet import api as wallet_api

from .models import Referral, ReferralCode, ReferralSettings

logger = logging.getLogger(__name__)

SESSION_CODE = "referral_code"
SESSION_AT = "referral_code_at"
QUERY_PARAM = "ref"
CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{4,20}$")
REWARD_EVENT = "referral.verified"
WELCOME_EVENT = "referral.welcome"
REFERRAL_REWARDED_EVENT = "referral.rewarded"


def get_settings() -> ReferralSettings:
    return ReferralSettings.get_solo()


def network_hash(request: HttpRequest) -> str:
    """Salted hash of the client network; the raw address is never stored."""
    ip = get_client_ip(request)
    if not ip:
        return ""
    return hashlib.sha256(f"{settings.SECRET_KEY}:referral:{ip}".encode()).hexdigest()


def get_or_create_code(user: AbstractBaseUser) -> ReferralCode:
    code, _ = ReferralCode.objects.get_or_create(user_id=user.pk)
    return code


def find_code(value: str) -> ReferralCode | None:
    """An active code by its text, or ``None`` for unknown or malformed input."""
    cleaned = (value or "").strip()
    if not CODE_PATTERN.match(cleaned):
        return None
    return (
        ReferralCode.objects.select_related("user")
        .filter(code=cleaned.upper(), is_active=True, user__is_active=True)
        .first()
    )


def capture_code(request: HttpRequest, value: str) -> bool:
    """Remember a valid code in the session until the visitor signs up."""
    if not get_settings().enabled:
        return False
    code = find_code(value)
    if code is None:
        return False
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.pk == code.user_id:
        return False
    ReferralCode.objects.filter(pk=code.pk).update(clicks=F("clicks") + 1)
    session = getattr(request, "session", None)
    if session is None:
        return False
    session[SESSION_CODE] = code.code
    session[SESSION_AT] = timezone.now().isoformat()
    return True


def captured_code(request: HttpRequest) -> str:
    """The code captured earlier in this session, if it has not expired."""
    session = getattr(request, "session", None)
    if session is None:
        return ""
    code = str(session.get(SESSION_CODE, "") or "")
    stamp = parse_datetime(str(session.get(SESSION_AT, "") or ""))
    if not code or stamp is None:
        return ""
    if timezone.now() - stamp > timedelta(days=get_settings().capture_days):
        return ""
    return code


def register_signup(
    invitee: AbstractBaseUser, request: HttpRequest | None
) -> Referral | None:
    """Link a freshly signed-up user to the referrer whose code the session captured."""
    if request is None or not get_settings().enabled:
        return None
    value = captured_code(request)
    code = find_code(value) if value else None
    if code is None or code.user_id == invitee.pk:
        return None
    with transaction.atomic():
        referral, created = Referral.objects.get_or_create(
            invitee_id=invitee.pk,
            defaults={
                "referrer_id": code.user_id,
                "code": code.code,
                "signup_network_hash": network_hash(request),
            },
        )
    session = getattr(request, "session", None)
    if session is not None:
        for key in (SESSION_CODE, SESSION_AT):
            session.pop(key, None)
    return referral if created else None


def _is_verified(user: AbstractBaseUser) -> bool:
    return bool(getattr(user, "email_verified_at", None))


def _shares_device(referrer: AbstractBaseUser, invitee: AbstractBaseUser) -> bool:
    """Same-device check through the devices contract when it exposes one."""
    check = AppService.export("devices", "users_share_device")
    return bool(callable(check) and check(referrer, invitee))


def _abuse_reason(referral: Referral, cfg: ReferralSettings) -> str:
    """A permanent reason to refuse this reward, or an empty string."""
    referrer = referral.referrer
    if referrer.pk == referral.invitee_id:
        return "self_referral"
    if not referrer.is_active:
        return "referrer_inactive"
    if cfg.block_same_network and referral.signup_network_hash:
        owner_hash = (
            ReferralCode.objects.filter(user=referrer)
            .values_list("network_hash", flat=True)
            .first()
        )
        farmed = (
            Referral.objects.filter(
                referrer=referrer,
                signup_network_hash=referral.signup_network_hash,
                status=Referral.Status.REWARDED,
            )
            .exclude(pk=referral.pk)
            .exists()
        )
        if owner_hash == referral.signup_network_hash or farmed:
            return "same_network"
    if _shares_device(referrer, referral.invitee):
        return "same_device"
    if (
        cfg.max_rewarded_per_referrer
        and Referral.objects.filter(
            referrer=referrer, status=Referral.Status.REWARDED
        ).count()
        >= cfg.max_rewarded_per_referrer
    ):
        return "referrer_cap_reached"
    return ""


def _daily_cap_reached(referral: Referral, cfg: ReferralSettings) -> bool:
    if not cfg.max_rewarded_per_referrer_per_day:
        return False
    start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        Referral.objects.filter(
            referrer_id=referral.referrer_id,
            status=Referral.Status.REWARDED,
            rewarded_at__gte=start,
        ).count()
        >= cfg.max_rewarded_per_referrer_per_day
    )


def evaluate_reward(
    invitee: AbstractBaseUser, *, verified: bool = False
) -> Referral | None:
    """Pay the referrer when the invitee has verified their email and qualified.

    Safe to call repeatedly (verification, approvals, admin sweeps): it only acts on a
    pending referral and the wallet key makes the payment exactly-once.
    """
    cfg = get_settings()
    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update(of=("self",))
            .select_related("referrer")
            .filter(invitee_id=invitee.pk)
            .first()
        )
        if referral is None or referral.status != Referral.Status.PENDING:
            return referral
        if not cfg.enabled or not (verified or _is_verified(invitee)):
            return referral
        if referral.email_verified_at is None:
            referral.email_verified_at = timezone.now()
        if cfg.require_qualifying_action and referral.qualified_at is None:
            referral.save(update_fields=["email_verified_at"])
            return referral
        reason = _abuse_reason(referral, cfg)
        if reason:
            return _reject(referral, reason)
        if _daily_cap_reached(referral, cfg):
            referral.save(update_fields=["email_verified_at"])
            return referral
        key = f"referral:{invitee.pk}"
        entry = wallet_api.grant_reward(
            referral.referrer,
            REWARD_EVENT,
            key=key,
            reference=key,
            context={"source_user_id": invitee.pk},
        )
        if entry is None:
            return _reject(referral, "reward_refused")
        wallet_api.grant_reward(
            invitee,
            WELCOME_EVENT,
            key=f"referral-welcome:{invitee.pk}",
            reference=key,
        )
        referral.status = Referral.Status.REWARDED
        referral.reward_credits = entry.amount
        referral.rewarded_at = timezone.now()
        referral.save(
            update_fields=[
                "email_verified_at",
                "status",
                "reward_credits",
                "rewarded_at",
            ]
        )
        payload = {
            "referrer_id": referral.referrer_id,
            "invitee_id": invitee.pk,
            "credits": entry.amount,
        }
        transaction.on_commit(
            lambda: event_bus.publish(REFERRAL_REWARDED_EVENT, payload)
        )
    return referral


def _reject(referral: Referral, reason: str) -> Referral:
    referral.status = Referral.Status.REJECTED
    referral.reject_reason = reason
    referral.save(update_fields=["status", "reject_reason", "email_verified_at"])
    logger.info("Referral %s not rewarded: %s", referral.pk, reason)
    return referral


def record_qualifying_action(
    user: AbstractBaseUser, kind: str = "approved_contribution"
) -> Referral | None:
    """Note the invitee's first approved contribution and re-evaluate the reward."""
    with transaction.atomic():
        referral = (
            Referral.objects.select_for_update(of=("self",))
            .filter(invitee_id=user.pk, status=Referral.Status.PENDING)
            .first()
        )
        if referral is None:
            return None
        if referral.qualified_at is None:
            referral.qualified_at = timezone.now()
            referral.qualifying_kind = kind[:40]
            referral.save(update_fields=["qualified_at", "qualifying_kind"])
    return evaluate_reward(user)


def handle_email_verified(user: AbstractBaseUser) -> None:
    """Called when the email is confirmed; the confirmation itself is the proof."""
    evaluate_reward(user, verified=True)


def handle_contribution_approved(data: Any) -> None:
    """Event-bus handler for ``contribution.approved`` (``{"user_id", "kind"}``)."""
    if not isinstance(data, dict) or data.get("user_id") is None:
        return
    pending = (
        Referral.objects.select_related("invitee")
        .filter(invitee_id=data["user_id"], status=Referral.Status.PENDING)
        .first()
    )
    if pending is not None:
        record_qualifying_action(pending.invitee, str(data.get("kind", "")))


def sweep_pending() -> int:
    """Re-evaluate pending referrals (for example after a daily cap reset)."""
    paid = 0
    for referral in Referral.objects.filter(
        status=Referral.Status.PENDING
    ).select_related("invitee"):
        result = evaluate_reward(referral.invitee)
        paid += int(result is not None and result.status == Referral.Status.REWARDED)
    return paid


@dataclass(frozen=True)
class ReferralSummary:
    code: ReferralCode
    invited: int
    rewarded: int
    pending: int
    credits_earned: int


def summary_for(
    user: AbstractBaseUser, request: HttpRequest | None = None
) -> ReferralSummary:
    code = get_or_create_code(user)
    if request is not None:
        current = network_hash(request)
        if current and current != code.network_hash:
            ReferralCode.objects.filter(pk=code.pk).update(network_hash=current)
    rows = Referral.objects.filter(referrer_id=user.pk)
    rewarded = rows.filter(status=Referral.Status.REWARDED)
    return ReferralSummary(
        code=code,
        invited=rows.count(),
        rewarded=rewarded.count(),
        pending=rows.filter(status=Referral.Status.PENDING).count(),
        credits_earned=sum(item.reward_credits for item in rewarded),
    )
