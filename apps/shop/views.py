from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import require_app_enabled
from apps.users.request_user import current_user

from . import services
from .models import BillingCycle, Order, PackageTier, Product, Subscription


def _shop_context(**extra):
    return {
        "online_payments_enabled": services.ONLINE_PAYMENTS_ENABLED,
        "billing_cycles": BillingCycle.choices,
        **extra,
    }


@require_GET
@require_app_enabled("shop")
def pricing(request: HttpRequest) -> HttpResponse:
    tiers = PackageTier.objects.filter(is_active=True).prefetch_related("products")
    current = (
        services.get_active_package(request.user)
        if request.user.is_authenticated
        else None
    )
    return render(
        request,
        "shop/pricing.html",
        _shop_context(
            tiers=tiers, current_tier_id=current.package_id if current else None
        ),
    )


@require_GET
@require_app_enabled("shop")
def product_list(request: HttpRequest) -> HttpResponse:
    products = Product.objects.filter(is_active=True)
    return render(request, "shop/product_list.html", _shop_context(products=products))


@require_GET
@require_app_enabled("shop")
def product_detail(request: HttpRequest, slug: str) -> HttpResponse:
    product = get_object_or_404(Product, slug=slug, is_active=True)
    return render(request, "shop/product_detail.html", _shop_context(product=product))


@require_POST
@require_app_enabled("shop")
@login_required
def place_order(request: HttpRequest, slug: str) -> HttpResponse:
    """Create a pending offline order; staff confirm payment and provision."""
    if not current_user(request).is_verified:
        messages.error(request, "Verify your email address before placing an order.")
        return redirect("shop:product_detail", slug=slug)
    product = get_object_or_404(Product, slug=slug, is_active=True)
    try:
        order = services.create_order(
            request.user, product, request.POST.get("billing_cycle", "monthly")
        )
    except services.OrderError as exc:
        messages.error(request, str(exc))
        return redirect("shop:product_detail", slug=slug)
    messages.success(
        request,
        f"Order #{order.pk} was received. Online payment is not available; "
        "our team will contact you with payment instructions.",
    )
    return redirect("shop:order_history")


@require_GET
@require_app_enabled("shop")
@login_required
def my_subscriptions(request: HttpRequest) -> HttpResponse:
    user = current_user(request)
    return render(
        request,
        "shop/my_subscriptions.html",
        _shop_context(
            package=services.get_active_package(user),
            entitlement=services.entitlement_summary(user),
            subscriptions=Subscription.objects.filter(user=user).select_related("plan"),
        ),
    )


@require_GET
@require_app_enabled("shop")
@login_required
def order_history(request: HttpRequest) -> HttpResponse:
    orders = Order.objects.filter(user=current_user(request)).prefetch_related(
        "items__product"
    )
    return render(request, "shop/order_history.html", _shop_context(orders=orders))


@require_GET
@require_app_enabled("shop")
@login_required
def order_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """One of the signed-in user's own orders; other users' orders are a 404."""
    order = get_object_or_404(
        Order.objects.prefetch_related("items__product"), pk=pk, user=request.user
    )
    return render(request, "shop/order_detail.html", _shop_context(order=order))
