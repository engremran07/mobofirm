"""Referral pages: personal link and invited users, plus the shareable landing URL."""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.core.decorators import require_app_enabled
from apps.users.request_user import current_user
from apps.wallet import api as wallet_api
from apps.wallet.models import RewardRule

from . import services
from .models import Referral

INVITEE_PAGE = 25


@require_GET
@require_app_enabled("referral")
def landing(request: HttpRequest, code: str) -> HttpResponse:
    """Shared link: remember the code for sign-up, then send the visitor there."""
    services.capture_code(request, code)
    return redirect("users:account_signup")


@login_required
@require_app_enabled("referral")
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    summary = services.summary_for(user, request)
    invitees = Referral.objects.filter(referrer=user).select_related("invitee")[
        :INVITEE_PAGE
    ]
    cfg = services.get_settings()
    rules = {
        rule.event_key: rule
        for rule in RewardRule.objects.filter(
            event_key__in=(services.REWARD_EVENT, services.WELCOME_EVENT)
        )
    }
    return render(
        request,
        "referral/dashboard.html",
        {
            "summary": summary,
            "invitees": invitees,
            "cfg": cfg,
            "referrer_rule": rules.get(services.REWARD_EVENT),
            "welcome_rule": rules.get(services.WELCOME_EVENT),
            "link": request.build_absolute_uri(
                reverse("referral:landing", args=[summary.code.code])
            ),
            "earn_url": wallet_api.earn_url(),
        },
    )
