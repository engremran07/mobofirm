from __future__ import annotations

from typing import Any, NamedTuple
from uuid import UUID

from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.core.decorators import require_app_enabled
from apps.users.request_user import current_user

from . import ad_unlock, entitlements, services, wallet_gateway
from . import credits as credit_purchase
from .models import Order, PackageTier, UserPackage


def _shop_context(**extra: Any) -> dict[str, Any]:
    return {
        "online_payments_enabled": services.ONLINE_PAYMENTS_ENABLED,
        "credits_available": wallet_gateway.available(),
        **extra,
    }


def _public_firmware(firmware_type: str, firmware_id: UUID) -> Any:
    """An approved, active firmware row of the given type, or 404."""
    if not django_apps.is_installed("apps.firmwares"):
        raise Http404
    from apps.firmwares import catalog

    model_class = catalog.get_firmware_model(firmware_type)
    if model_class is None:
        raise Http404
    firmware = (
        catalog.get_public_firmware_queryset(model_class).filter(pk=firmware_id).first()
    )
    if firmware is None:
        raise Http404
    return firmware


def _orderable_package(slug: str) -> PackageTier:
    return get_object_or_404(
        PackageTier, slug=slug, is_active=True, is_public=True, is_default=False
    )


def _free_unlock_cell(package: PackageTier) -> str:
    """Pricing-row text: what an over-limit account can do on this package."""
    if package.ad_free:
        return "No ads to watch"
    if package.unlock_offers_free:
        count = package.unlock_ads_free
        return f"Watch {count} ad{'' if count == 1 else 's'} to unlock a file"
    return "Wait for the next period"


def _featured_unlock_cell(package: PackageTier) -> str:
    if package.applies_to_featured:
        return "Included"
    if package.unlock_offers_featured:
        count = package.unlock_ads_featured
        return f"Watch {count} ad{'' if count == 1 else 's'} per file"
    return "—"


def comparison_rows(packages: list[PackageTier]) -> list[tuple[str, list[Any]]]:
    """Rows of the pricing comparison table; ``bool`` cells render as a check or dash."""

    def column(getter: Any) -> list[Any]:
        return [getter(package) for package in packages]

    return [
        ("Price", column(lambda p: p.price_text)),
        ("Duration", column(lambda p: p.duration_text)),
        ("Download quota", column(lambda p: p.quota_text)),
        ("Extra every day", column(lambda p: p.extra_text or "—")),
        ("Fair use limits", column(lambda p: p.fair_text or "—")),
        ("Simultaneous downloads", column(lambda p: p.concurrency_text)),
        ("Wait between files", column(lambda p: p.cooldown_text)),
        ("Devices", column(lambda p: p.devices_text)),
        ("Device slot reset", column(lambda p: p.slot_reset_text)),
        ("Free files", column(lambda p: p.applies_to_free)),
        ("Featured files", column(lambda p: p.applies_to_featured)),
        ("Featured blog articles", column(lambda p: p.blog_text)),
        (
            "Paid files",
            column(
                lambda p: (
                    f"{p.paid_discount_percent}% off"
                    if p.applies_to_paid and p.paid_discount_percent
                    else p.applies_to_paid
                )
            ),
        ),
        ("Ad-free downloads", column(lambda p: p.ad_free)),
        ("Ads", column(lambda p: p.ads_text)),
        ("Over your limit", column(_free_unlock_cell)),
        ("Featured files by ads", column(_featured_unlock_cell)),
        ("Priority queue", column(lambda p: p.priority_queue)),
        ("Resumable downloads", column(lambda p: p.resume_support)),
        ("Direct links", column(lambda p: p.direct_links)),
        ("Early access", column(lambda p: p.early_access)),
    ]


@require_GET
@require_app_enabled("shop")
def pricing(request: HttpRequest) -> HttpResponse:
    packages = list(
        PackageTier.objects.filter(is_active=True, is_public=True).order_by(
            "sort_order", "price", "name"
        )
    )
    current = entitlements.effective_entitlements(request.user)
    return render(
        request,
        "shop/pricing.html",
        _shop_context(
            packages=packages,
            rows=comparison_rows(packages),
            current_package_id=current.tier.pk if current.tier else None,
            current_expires_at=current.package.expires_at if current.package else None,
        ),
    )


@require_GET
@require_app_enabled("shop")
def package_detail(request: HttpRequest, slug: str) -> HttpResponse:
    package = get_object_or_404(PackageTier, slug=slug, is_active=True, is_public=True)
    balance = None
    if request.user.is_authenticated and wallet_gateway.available():
        balance = wallet_gateway.balance(request.user)
    return render(
        request,
        "shop/package_detail.html",
        _shop_context(
            package=package,
            orderable=not package.is_default and package.price > 0,
            credit_balance=balance,
        ),
    )


@require_POST
@require_app_enabled("shop")
@login_required
def place_order(request: HttpRequest, slug: str) -> HttpResponse:
    """Create a pending offline order; staff confirm payment and provision."""
    if not current_user(request).is_verified:
        messages.error(request, "Verify your email address before placing an order.")
        return redirect("shop:package_detail", slug=slug)
    package = _orderable_package(slug)
    try:
        order = services.create_order(request.user, package)
    except services.OrderError as exc:
        messages.error(request, str(exc))
        return redirect("shop:package_detail", slug=slug)
    messages.success(
        request,
        f"Order #{order.pk} was received. Online payment is not available; "
        "our team will contact you with payment instructions.",
    )
    return redirect("shop:order_detail", pk=order.pk)


@require_POST
@require_app_enabled("shop")
@login_required
def request_file(
    request: HttpRequest, firmware_type: str, firmware_id: UUID
) -> HttpResponse:
    """Request to buy one paid firmware file; staff confirm payment offline."""
    firmware = _public_firmware(firmware_type, firmware_id)
    content_type, object_id = entitlements.file_ref(firmware)
    links = entitlements.file_links(content_type.pk, object_id)
    back = links[0] if links else reverse("shop:pricing")
    if not current_user(request).is_verified:
        messages.error(request, "Verify your email address before requesting a file.")
        return redirect(back)
    try:
        order = entitlements.request_purchase(request.user, firmware)
    except entitlements.PurchaseError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    messages.success(
        request,
        f"Order #{order.pk} for {firmware.original_file_name} was received. Online "
        "payment is not available; our team will confirm payment, and the file "
        "unlocks once they do.",
    )
    return redirect("shop:order_detail", pk=order.pk)


class _CreditsDialog(NamedTuple):
    title: str
    what: str
    credit_cost: int
    action: str
    back: str
    request_id: str = ""


def _credits_dialog(
    request: HttpRequest, dialog: _CreditsDialog, *, error: str = "", status: int = 200
) -> HttpResponse:
    """The confirm dialog: a partial for HTMX, a full page without JavaScript."""
    balance = wallet_gateway.balance(request.user)
    context = _shop_context(
        title=dialog.title,
        what=dialog.what,
        credit_cost=dialog.credit_cost,
        credit_balance=balance,
        missing=max(0, dialog.credit_cost - balance),
        action=dialog.action,
        back_url=dialog.back,
        earn_url=wallet_gateway.earn_url(),
        request_id=dialog.request_id,
        error=error,
    )
    template = (
        "shop/dialogs/pay_with_credits.html"
        if request.headers.get("HX-Request")
        else "shop/credits_confirm.html"
    )
    return render(request, template, context, status=status)


def _credits_gate(request: HttpRequest, back: str) -> HttpResponse | None:
    if not wallet_gateway.available():
        messages.error(request, "Credits are not available on this site right now.")
        return redirect(back)
    if not current_user(request).is_verified:
        messages.error(request, "Verify your email address before paying.")
        return redirect(back)
    return None


def _posted_request_id(request: HttpRequest) -> str:
    """The confirm dialog's one-purchase token, or ``""`` when it is missing/forged."""
    value = str(request.POST.get("request_id", ""))
    return value if credit_purchase.is_request_id(value) else ""


@require_http_methods(["GET", "POST"])
@require_app_enabled("shop")
@login_required
def package_credits(request: HttpRequest, slug: str) -> HttpResponse:
    """Confirm (GET) and perform (POST) buying a package with credits."""
    package = _orderable_package(slug)
    back = reverse("shop:package_detail", kwargs={"slug": slug})
    blocked = _credits_gate(request, back)
    if blocked is not None:
        return blocked
    cost = credit_purchase.credits_for_package(package, request.user)
    action = reverse("shop:package_credits", kwargs={"slug": slug})
    request_id = (
        _posted_request_id(request)
        if request.method == "POST"
        else credit_purchase.new_request_id()
    )
    dialog = _CreditsDialog(
        title=f"Buy {package.name} with credits",
        what=f"{package.name} · {package.summary()}",
        credit_cost=cost,
        action=action,
        back=back,
        request_id=request_id,
    )
    if request.method == "GET":
        if cost <= 0:
            messages.error(request, "This package cannot be bought with credits.")
            return redirect(back)
        return _credits_dialog(request, dialog)
    if not request_id:
        messages.error(request, "This confirmation expired. Please confirm again.")
        return redirect(back)
    try:
        order = credit_purchase.pay_package_with_credits(
            request.user, package, request_id=request_id
        )
    except wallet_gateway.InsufficientCreditsError as exc:
        return _credits_dialog(request, dialog, error=str(exc), status=402)
    except (entitlements.PurchaseError, wallet_gateway.CreditsUnavailable) as exc:
        messages.error(request, str(exc))
        return redirect(back)
    messages.success(
        request, f"{package.name} is active. {order.credits_spent} credits were spent."
    )
    return redirect("shop:order_detail", pk=order.pk)


@require_http_methods(["GET", "POST"])
@require_app_enabled("shop")
@login_required
def file_credits(
    request: HttpRequest, firmware_type: str, firmware_id: UUID
) -> HttpResponse:
    """Confirm (GET) and perform (POST) buying one paid or featured file with credits."""
    firmware = _public_firmware(firmware_type, firmware_id)
    content_type, object_id = entitlements.file_ref(firmware)
    links = entitlements.file_links(content_type.pk, object_id)
    back = links[0] if links else reverse("shop:pricing")
    blocked = _credits_gate(request, back)
    if blocked is not None:
        return blocked
    action = reverse(
        "shop:file_credits",
        kwargs={"firmware_type": firmware_type, "firmware_id": firmware_id},
    )
    request_id = (
        _posted_request_id(request)
        if request.method == "POST"
        else credit_purchase.new_request_id()
    )
    if request.method == "GET":
        problem = credit_purchase.purchase_block_reason(request.user, firmware)
        cost = credit_purchase.credits_for_file(request.user, firmware)
        if problem or cost <= 0:
            messages.error(
                request, problem or "This file cannot be bought with credits."
            )
            return redirect(back)
        return _credits_dialog(
            request,
            _CreditsDialog(
                title="Buy this file with credits",
                what=str(firmware.original_file_name),
                credit_cost=cost,
                action=action,
                back=back,
                request_id=request_id,
            ),
        )
    if not request_id:
        messages.error(request, "This confirmation expired. Please confirm again.")
        return redirect(back)
    try:
        order = credit_purchase.pay_file_with_credits(
            request.user, firmware, request_id=request_id
        )
    except wallet_gateway.InsufficientCreditsError as exc:
        dialog = _CreditsDialog(
            title="Buy this file with credits",
            what=str(firmware.original_file_name),
            credit_cost=credit_purchase.credits_for_file(request.user, firmware),
            action=action,
            back=back,
            request_id=request_id,
        )
        return _credits_dialog(request, dialog, error=str(exc), status=402)
    except (entitlements.PurchaseError, wallet_gateway.CreditsUnavailable) as exc:
        messages.error(request, str(exc))
        return redirect(back)
    messages.success(
        request,
        f"{firmware.original_file_name} is unlocked. {order.credits_spent} credits "
        "were spent.",
    )
    return redirect(back if links else reverse("shop:order_detail", args=[order.pk]))


def _file_back(firmware: Any) -> str:
    content_type, object_id = entitlements.file_ref(firmware)
    links = entitlements.file_links(content_type.pk, object_id)
    return links[0] if links else reverse("shop:pricing")


def _unlock_panel(
    request: HttpRequest, firmware: Any, *, status: int = 200
) -> HttpResponse:
    """The unlock panel as an HTMX fragment (always 200 so the swap shows the state)."""
    context = ad_unlock.panel_context(
        request.user, firmware, back_url=_file_back(firmware)
    )
    return render(request, "shop/_ad_unlock_panel.html", context, status=status)


@require_GET
@require_app_enabled("shop")
@login_required
def unlock_panel(
    request: HttpRequest, firmware_type: str, firmware_id: UUID
) -> HttpResponse:
    """The state-aware "unlock by watching ads" panel of one file (HTMX refresh)."""
    return _unlock_panel(request, _public_firmware(firmware_type, firmware_id))


@require_POST
@require_app_enabled("shop")
@login_required
def unlock_start(
    request: HttpRequest, firmware_type: str, firmware_id: UUID
) -> HttpResponse:
    """Start (or resume) the ad unlock of one file, then show where it stands.

    HTMX gets the refreshed panel; a plain form post redirects back to the file page.
    """
    firmware = _public_firmware(firmware_type, firmware_id)
    back = _file_back(firmware)
    try:
        ad_unlock.start_ticket(request.user, firmware)
    except ad_unlock.UnlockRefused as refusal:
        if request.headers.get("HX-Request"):
            return _unlock_panel(request, firmware)
        messages.error(request, refusal.message)
        return redirect(back)
    if request.headers.get("HX-Request"):
        return _unlock_panel(request, firmware)
    return redirect(back)


@require_GET
@require_app_enabled("shop")
@login_required
def my_package(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    return render(
        request,
        "shop/my_package.html",
        _shop_context(
            package=entitlements.active_package(user),
            entitlement=entitlements.entitlement_summary(user),
            history=UserPackage.objects.filter(user=user, is_baseline=False)
            .select_related("package")
            .order_by("-started_at")[:10],
        ),
    )


@require_GET
@require_app_enabled("shop")
@login_required
def order_history(request: HttpRequest) -> HttpResponse:
    orders = Order.objects.filter(user=current_user(request)).prefetch_related("items")
    return render(request, "shop/order_history.html", _shop_context(orders=orders))


@require_GET
@require_app_enabled("shop")
@login_required
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One of the signed-in user's own orders; other users' orders are a 404."""
    order = get_object_or_404(
        Order.objects.prefetch_related("items__package"), pk=pk, user=request.user
    )
    return render(request, "shop/order_detail.html", _shop_context(order=order))
