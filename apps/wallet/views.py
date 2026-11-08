"""Public wallet pages. Every query is scoped to ``request.user``; ids never leak."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from apps.core.app_service import AppService
from apps.core.decorators import require_app_enabled
from apps.users.decorators import email_verification_required
from apps.users.request_user import current_user

from . import registry, services
from .forms import LedgerFilterForm, PayoutForm, TopUpForm
from .models import (
    CreditPrice,
    LedgerEntry,
    PayoutRequest,
    RewardRule,
    TopUpRequest,
)

LEDGER_PAGE_SIZE = 20


def _base_context(request: HttpRequest, tab: str) -> dict[str, object]:
    user = current_user(request)
    return {
        "wallet_tab": tab,
        "snapshot": services.balance_snapshot(user),
        "cfg": services.get_settings(),
        "earn_url": reverse("wallet:earn"),
    }


@login_required
@require_app_enabled("wallet")
@require_GET
def dashboard(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    context = _base_context(request, "overview")
    wallet = services.get_wallet(user)
    context.update(
        recent=LedgerEntry.objects.filter(wallet=wallet)[:8],
        open_topups=TopUpRequest.objects.filter(
            user=user, status=TopUpRequest.Status.PENDING
        ),
        open_payouts=PayoutRequest.objects.filter(
            wallet=wallet,
            status__in=(PayoutRequest.Status.PENDING, PayoutRequest.Status.APPROVED),
        ),
    )
    return render(request, "wallet/dashboard.html", context)


@login_required
@require_app_enabled("wallet")
@require_GET
def ledger(request: HttpRequest) -> HttpResponse:
    wallet = services.get_wallet(current_user(request))
    form = LedgerFilterForm(request.GET or None)
    entries = LedgerEntry.objects.filter(wallet=wallet)
    if form.is_valid():
        if form.cleaned_data["direction"]:
            entries = entries.filter(direction=form.cleaned_data["direction"])
        if form.cleaned_data["reason"]:
            entries = entries.filter(reason=form.cleaned_data["reason"])
    page = Paginator(entries, LEDGER_PAGE_SIZE).get_page(request.GET.get("page"))
    context = _base_context(request, "ledger")
    context.update(form=form, page_obj=page)
    template = (
        "wallet/_ledger_results.html"
        if request.headers.get("HX-Request")
        else "wallet/ledger.html"
    )
    return render(request, template, context)


@login_required
@require_app_enabled("wallet")
@require_GET
def earn(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, "earn")
    context["rules"] = RewardRule.objects.filter(enabled=True).order_by("app", "label")
    promotions = AppService.get("promotions")
    context["offers"] = (
        promotions.offers_for_user(request.user) if promotions is not None else []
    )
    return render(request, "wallet/earn.html", context)


@login_required
@require_app_enabled("wallet")
@require_GET
def spend(request: HttpRequest) -> HttpResponse:
    context = _base_context(request, "spend")
    sinks = registry.spend_sinks()
    rows = []
    for price in CreditPrice.objects.filter(enabled=True).order_by("app", "label"):
        sink = sinks.get(price.sink_key)
        amount = price.credits
        if sink is not None and sink.user_price is not None:
            personal = sink.user_price(request.user)
            amount = amount if personal is None else personal
        rows.append(
            {
                "label": price.label,
                "credits": amount,
                "derived": bool(sink and sink.derived),
            }
        )
    context["prices"] = rows
    return render(request, "wallet/spend.html", context)


@login_required
@require_app_enabled("wallet")
@email_verification_required
@require_http_methods(["GET", "POST"])
def topup(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    form = TopUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            created = services.request_topup(user, form.cleaned_data["credits"])
        except services.TopUpError as error:
            form.add_error("credits", str(error))
        else:
            messages.success(
                request,
                f"Top-up #{created.pk} requested. Our team confirms it after payment.",
            )
            return redirect("wallet:topup")
    context = _base_context(request, "topup")
    context.update(form=form, topups=TopUpRequest.objects.filter(user=user)[:20])
    return render(request, "wallet/topup.html", context)


@login_required
@require_app_enabled("wallet")
@require_POST
def cancel_topup(request: HttpRequest, pk: int) -> HttpResponse:
    user = current_user(request)
    if not TopUpRequest.objects.filter(pk=pk, user=user).exists():
        raise Http404
    try:
        services.cancel_topup(pk, user=user)
    except services.TopUpError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Top-up request cancelled.")
    return redirect("wallet:topup")


@login_required
@require_app_enabled("wallet")
@email_verification_required
@require_http_methods(["GET", "POST"])
def payouts(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    form = PayoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            created = services.request_payout(
                user,
                form.cleaned_data["credits"],
                method=form.cleaned_data["method"],
                destination=form.cleaned_data["destination"],
            )
        except services.InsufficientCredits as error:
            form.add_error("credits", f"You can withdraw up to {error.available}.")
        except (services.PayoutError, services.WalletFrozen) as error:
            form.add_error(None, str(error))
        else:
            messages.success(
                request, f"Payout #{created.pk} requested. Staff review it shortly."
            )
            return redirect("wallet:payouts")
    wallet = services.get_wallet(user)
    context = _base_context(request, "payouts")
    context.update(form=form, payouts=PayoutRequest.objects.filter(wallet=wallet)[:20])
    return render(request, "wallet/payouts.html", context)
