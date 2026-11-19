"""Self-service account erasure: request, email confirmation, grace period, cancel."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.users.request_user import current_user
from apps.users.services.erasure import (
    CONFIRMATION_HOURS,
    REFUSAL_MESSAGES,
    ErasureBlocked,
    ErasureRefused,
    cancel_erasure,
    confirm_self_service_erasure,
    grace_period,
    open_erasure_for,
    refusal_reason,
    start_self_service_erasure,
)
from apps.users.services.rate_limit import allow_action

_ATTEMPT_LIMIT = 5
_ATTEMPT_WINDOW_SECONDS = 900


def _refusal_message(exc: ErasureRefused) -> str:
    message = REFUSAL_MESSAGES.get(exc.code, "This request could not be completed.")
    if isinstance(exc, ErasureBlocked):
        return f"{message} {exc.summary}."
    return message


def _attempts_allowed(request: HttpRequest, scope: str) -> bool:
    return allow_action(
        f"users:erasure:{scope}:{current_user(request).pk}",
        max_attempts=_ATTEMPT_LIMIT,
        window_seconds=_ATTEMPT_WINDOW_SECONDS,
        fail_open=False,
    )


@login_required(login_url="users:account_login")
@require_http_methods(["GET", "POST"])
@never_cache
def account_erasure(request: HttpRequest) -> HttpResponse:
    """Show the account's erasure state; POST requests it or cancels it."""
    user = current_user(request)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "request":
            _request_erasure(request)
        elif action == "cancel":
            _cancel_erasure(request)
        else:
            return HttpResponse(status=400)
        return redirect("users:account_erasure")

    return render(
        request,
        "users/account_erasure.html",
        {
            "erasure": open_erasure_for(user),
            "blocked": REFUSAL_MESSAGES.get(refusal_reason(user), ""),
            "needs_password": user.has_usable_password(),
            "grace_days": grace_period().days,
            "confirmation_hours": CONFIRMATION_HOURS,
        },
    )


def _request_erasure(request: HttpRequest) -> None:
    user = current_user(request)
    if not _attempts_allowed(request, "request"):
        messages.error(request, "Too many attempts. Try again later.")
        return
    if user.has_usable_password() and not user.check_password(
        request.POST.get("password", "")
    ):
        messages.error(request, "Your password is not correct.")
        return
    try:
        start_self_service_erasure(
            user,
            build_link=lambda token: request.build_absolute_uri(
                reverse("users:account_erasure_confirm", args=[token])
            ),
        )
    except ErasureRefused as exc:
        messages.error(request, _refusal_message(exc))
        return
    messages.success(
        request,
        "We emailed you a confirmation link. Nothing is erased until you confirm it.",
    )


def _cancel_erasure(request: HttpRequest) -> None:
    user = current_user(request)
    erasure = open_erasure_for(user)
    if erasure is None:
        messages.info(request, "There is no open erasure request.")
        return
    try:
        cancel_erasure(erasure, by=user)
    except ErasureRefused as exc:
        messages.error(request, _refusal_message(exc))
        return
    messages.success(request, "Your erasure request was cancelled.")


@login_required(login_url="users:account_login")
@require_http_methods(["GET", "POST"])
@never_cache
def account_erasure_confirm(request: HttpRequest, token: str) -> HttpResponse:
    """The emailed link: GET only explains; the POST confirms (no GET side effects)."""
    user = current_user(request)
    if request.method == "POST":
        if not _attempts_allowed(request, "confirm"):
            messages.error(request, "Too many attempts. Try again later.")
            return redirect("users:account_erasure")
        try:
            confirm_self_service_erasure(user, token)
        except ErasureRefused as exc:
            messages.error(request, _refusal_message(exc))
        else:
            messages.success(
                request,
                "Erasure confirmed. You can cancel it until the grace period ends.",
            )
        return redirect("users:account_erasure")
    return render(
        request,
        "users/account_erasure_confirm.html",
        {"grace_days": grace_period().days},
    )
