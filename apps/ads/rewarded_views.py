"""Rewarded-ad pages and JSON endpoints (thin: every rule lives in ``rewarded``)."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.ads import rewarded
from apps.ads.models import RewardedAdConfig
from apps.consent.constants import ADVERTISING
from apps.consent.services import has_consent
from apps.core.dialogs import safe_next_url

MAX_TOKEN_LENGTH = 128


def _json_login_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """401 JSON for the fetch endpoints instead of a redirect to the sign-in page."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_authenticated:
            return JsonResponse(
                {"ok": False, "error": "login_required", "message": "Sign in first."},
                status=401,
            )
        return view(request, *args, **kwargs)

    return wrapped


def _error(error: rewarded.RewardedAdError) -> JsonResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "error": error.code,
        "message": error.message,
    }
    if error.retry_after is not None:
        payload["retry_after"] = error.retry_after
    if error.remaining is not None:
        payload["remaining_seconds"] = error.remaining
    response = JsonResponse(payload, status=error.status)
    if error.retry_after is not None:
        response["Retry-After"] = str(error.retry_after)
    return response


def _token(request: HttpRequest) -> str:
    return str(request.POST.get("token", ""))[:MAX_TOKEN_LENGTH]


def _state(request: HttpRequest, config: RewardedAdConfig) -> dict[str, Any]:
    """Why the user cannot start (or that they can), as the page shows it."""
    unavailable = rewarded.unavailable_reason(config)
    if unavailable:
        return {"ready": False, "notice": unavailable, "notice_kind": "unavailable"}
    if not getattr(request.user, "email_verified_at", None):
        return {
            "ready": False,
            "notice": "Verify your email address to watch rewarded ads.",
            "notice_kind": "verify",
        }
    if not has_consent(request, ADVERTISING):
        return {
            "ready": False,
            "notice": "Rewarded ads need your advertising consent.",
            "notice_kind": "consent",
        }
    try:
        rewarded.check_eligibility(request, request.user, config)
    except rewarded.RewardedAdError as error:
        return {"ready": False, "notice": error.message, "notice_kind": error.code}
    return {"ready": True, "notice": "", "notice_kind": ""}


def _back_url(request: HttpRequest, config: RewardedAdConfig) -> str:
    fallback = (
        reverse("devices:my_devices")
        if config.reward_type == RewardedAdConfig.RewardType.DEVICE_RESET
        else reverse("users:dashboard")
    )
    return safe_next_url(request, fallback) or fallback


@login_required(login_url="users:account_login")
@require_GET
def rewarded_page(request: HttpRequest, config_id: int) -> HttpResponse:
    config = get_object_or_404(
        RewardedAdConfig.objects.select_related("placement"),
        pk=config_id,
        is_deleted=False,
    )
    state = _state(request, config)
    exposed = state["ready"] or state["notice_kind"] != "unavailable"
    context: dict[str, Any] = {
        **state,
        "config": config if exposed else None,
        "placement_code": config.placement.code
        if state["ready"] and config.placement
        else "",
        "min_watch": rewarded.effective_min_watch(config),
        "back_url": _back_url(request, config),
        "privacy_url": reverse("consent:privacy_centre"),
        "start_url": reverse("ads:rewarded_start", kwargs={"config_id": config.pk}),
        "heartbeat_url": reverse("ads:rewarded_heartbeat"),
        "complete_url": reverse("ads:rewarded_complete"),
    }
    return render(request, "ads/rewarded.html", context)


@_json_login_required
@csrf_protect
@require_POST
def rewarded_start(request: HttpRequest, config_id: int) -> JsonResponse:
    config = get_object_or_404(
        RewardedAdConfig.objects.select_related("placement"),
        pk=config_id,
        is_deleted=False,
    )
    try:
        result = rewarded.start(request, request.user, config)
    except rewarded.RewardedAdError as error:
        return _error(error)
    return JsonResponse(
        {
            "ok": True,
            "token": result.token,
            "min_watch_seconds": result.min_watch_seconds,
            "heartbeat_seconds": result.heartbeat_seconds,
            "expires_in": result.expires_in,
        }
    )


@_json_login_required
@csrf_protect
@require_POST
def rewarded_heartbeat(request: HttpRequest) -> JsonResponse:
    claimed_raw = request.POST.get("claimed")
    claimed: float | None = None
    if claimed_raw not in (None, ""):
        try:
            claimed = float(str(claimed_raw))
        except ValueError:
            return JsonResponse(
                {"ok": False, "error": "invalid_input", "message": "Bad request."},
                status=400,
            )
    try:
        progress = rewarded.heartbeat(
            request.user, _token(request), claimed_seconds=claimed
        )
    except rewarded.RewardedAdError as error:
        return _error(error)
    return JsonResponse({"ok": True, **progress})


@_json_login_required
@csrf_protect
@require_POST
def rewarded_complete(request: HttpRequest) -> JsonResponse:
    try:
        result = rewarded.complete(request.user, _token(request))
    except rewarded.RewardedAdError as error:
        return _error(error)
    return JsonResponse(
        {
            "ok": True,
            "reward_granted": result.reward_granted,
            "reward_type": result.reward_type,
            "reward_amount": result.reward_amount,
            "message": result.message,
            "already_completed": result.already_completed,
        }
    )
