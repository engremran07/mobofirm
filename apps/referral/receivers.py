"""Referral signal wiring: sign-up capture, email verification and approved work."""

from __future__ import annotations

from typing import Any

from allauth.account.signals import email_confirmed, user_signed_up
from django.http import HttpRequest

from apps.core.events.bus import EventTypes, event_bus

from . import services

CONTRIBUTION_APPROVED_EVENT = "contribution.approved"


def on_user_signed_up(
    request: HttpRequest | None = None, user: Any = None, **kwargs: Any
) -> None:
    if user is not None:
        services.register_signup(user, request)


def on_email_confirmed(email_address: Any = None, **kwargs: Any) -> None:
    if email_address is not None:
        services.handle_email_verified(email_address.user)


def on_user_email_verified(data: Any) -> None:
    """Event-bus form of verification: payload ``{"user_id": int}``."""
    if not isinstance(data, dict) or data.get("user_id") is None:
        return
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=data["user_id"]).first()
    if user is not None:
        services.handle_email_verified(user)


def connect_receivers() -> None:
    user_signed_up.connect(on_user_signed_up, dispatch_uid="referral_signed_up_v1")
    email_confirmed.connect(
        on_email_confirmed, dispatch_uid="referral_email_confirmed_v1"
    )
    event_bus.subscribe(EventTypes.USER_EMAIL_VERIFIED, on_user_email_verified)
    event_bus.subscribe(
        CONTRIBUTION_APPROVED_EVENT, services.handle_contribution_approved
    )
