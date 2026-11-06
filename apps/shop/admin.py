"""Shop administration (Unfold): packages, plans, products and offline orders.

Online payments are disabled by owner decision, so orders are confirmed by staff
after offline payment. ``mark_paid`` and ``cancel`` always go through
``apps.shop.services`` so provisioning stays exactly-once and permission-checked.
"""

from __future__ import annotations

from typing import Any, ClassVar

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import Model as DjangoModel
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextInputWidget

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import services
from .models import (
    Order,
    OrderItem,
    PackageQuotaOverride,
    PackageTier,
    Product,
    Subscription,
    SubscriptionPlan,
    UserPackage,
)

STATUS_BADGES = {
    "pending": "warning",
    "paid": "success",
    "refunded": "info",
    "cancelled": "danger",
    "active": "success",
    "trialing": "info",
    "expired": "warning",
    "suspended": "danger",
}


class MarkPaidForm(BaseDialogForm):
    reference = forms.CharField(
        label=_("Payment reference"),
        required=False,
        max_length=200,
        help_text=_("Receipt or transfer reference, kept on the order."),
        widget=UnfoldAdminTextInputWidget,
    )


@register(SubscriptionPlan)
class SubscriptionPlanAdmin(ModelAdmin):
    list_display = ("name", "price_monthly", "price_yearly", "is_active", "sort_order")
    list_filter = ("is_active",)
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@register(PackageTier)
class PackageTierAdmin(ModelAdmin):
    list_display = (
        "name",
        "tier_level",
        "price_monthly",
        "daily_download_limit",
        "monthly_download_limit",
        "is_default",
        "is_active",
    )
    list_filter = (
        ("tier_level", ChoicesDropdownFilter),
        "is_active",
        "is_default",
        "is_featured",
    )
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (
            _("Overview"),
            {
                "fields": (
                    "name",
                    "slug",
                    "tier_level",
                    "description",
                    "is_active",
                    "is_default",
                    "is_featured",
                    "sort_order",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Pricing"),
            {
                "fields": (
                    "price_monthly",
                    "price_quarterly",
                    "price_yearly",
                    "price_lifetime",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Download limits"),
            {
                "fields": (
                    "daily_download_limit",
                    "monthly_download_limit",
                    "max_file_size_mb",
                    "daily_bandwidth_mb",
                    "monthly_bandwidth_mb",
                    "max_download_speed_kbps",
                    "max_concurrent_downloads",
                    "cooldown_seconds",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Features and access"),
            {
                "fields": (
                    "ad_free",
                    "priority_queue",
                    "resume_support",
                    "api_access",
                    "early_access",
                    "direct_links",
                    "access_official",
                    "access_engineering",
                    "access_readback",
                    "access_modified",
                    "extra_features",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )


@register(UserPackage)
class UserPackageAdmin(ModelAdmin):
    list_display = (
        "user",
        "package",
        "status_badge",
        "billing_cycle",
        "expires_at",
        "downloads_today",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("package", RelatedDropdownFilter),
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)
    list_select_related = ("user", "package")
    readonly_fields = (
        "downloads_today",
        "downloads_this_month",
        "bandwidth_today_mb",
        "bandwidth_this_month_mb",
        "last_download_at",
        "cancelled_at",
    )
    actions = ("cancel_selected",)
    actions_row = ("cancel_package",)
    actions_detail = ("cancel_package",)

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: UserPackage) -> str:
        return obj.status

    @action(description=_("Cancel selected packages"), permissions=["change"])
    def cancel_selected(
        self, request: HttpRequest, queryset: QuerySet[UserPackage]
    ) -> None:
        live = queryset.filter(
            status__in=[UserPackage.Status.ACTIVE, UserPackage.Status.TRIALING]
        )
        count = sum(bool(services.cancel_package(package)) for package in live)
        self.message_user(
            request, _("%(count)d package(s) cancelled.") % {"count": count}
        )

    @action(
        description=_("Cancel package"),
        icon="cancel",
        permissions=["change"],
        url_path="cancel-package",
        dialog=confirm_dialog(
            _("Cancel this package"),
            _("The user loses the package's limits and features immediately."),
            submit=_("Cancel package"),
        ),
    )
    def cancel_package(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        package = self.get_object(request, unquote(object_id))
        if package is None:
            raise Http404
        if package.status in (UserPackage.Status.ACTIVE, UserPackage.Status.TRIALING):
            services.cancel_package(package)
            self.message_user(request, _("Package cancelled."))
        else:
            self.message_user(
                request,
                _("Only active or trialing packages can be cancelled."),
                messages.ERROR,
            )
        return htmx_redirect(return_url(request, change_url(self, package.pk)))


@register(PackageQuotaOverride)
class PackageQuotaOverrideAdmin(ModelAdmin):
    list_display = ("user", "reason", "granted_by", "expires_at")
    list_filter = (("expires_at", RangeDateTimeFilter),)
    search_fields = ("user__email", "user__username", "reason")
    raw_id_fields = ("user", "granted_by")
    readonly_fields = ("created_at",)

    def save_model(
        self, request: HttpRequest, obj: PackageQuotaOverride, form: Any, change: bool
    ) -> None:
        if not change:
            obj.granted_by_id = request.user.pk
        super().save_model(request, obj, form, change)


@register(Product)
class ProductAdmin(ModelAdmin):
    list_display = ("name", "product_type", "price", "is_active")
    list_filter = (("product_type", ChoicesDropdownFilter), "is_active")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = ("user", "plan", "status_badge", "expires_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("plan", RelatedDropdownFilter),
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: Subscription) -> str:
        return obj.status


class OrderItemInline(TabularInline):
    model = OrderItem
    can_delete = False
    tab = True
    ordering = ("id",)
    readonly_fields = (
        "product",
        "billing_cycle",
        "quantity",
        "unit_price",
        "line_total",
    )

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(Order)
class OrderAdmin(NoAddModelAdmin):
    list_display = (
        "id",
        "user",
        "status_badge",
        "total",
        "currency",
        "payment_method",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "payment_reference")
    list_select_related = ("user",)
    readonly_fields = (
        "user",
        "status",
        "total",
        "currency",
        "paid_at",
        "fulfilled_at",
        "processed_by",
        "created_at",
        "updated_at",
    )
    inlines = (OrderItemInline,)
    actions = ("mark_paid", "cancel_orders")
    actions_row = ("mark_order_paid", "cancel_order")
    actions_detail = ("mark_order_paid", "cancel_order")

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: Order) -> str:
        return obj.status

    def _mark_paid(self, request: HttpRequest, order_id: int, reference: str) -> bool:
        try:
            services.mark_order_paid(
                order_id, staff_user=request.user, reference=reference
            )
        except services.OrderError as exc:
            self.message_user(request, f"Order #{order_id}: {exc}", messages.ERROR)
            return False
        return True

    def _cancel(self, request: HttpRequest, order_id: int) -> bool:
        try:
            services.cancel_order(order_id, staff_user=request.user)
        except services.OrderError as exc:
            self.message_user(request, f"Order #{order_id}: {exc}", messages.ERROR)
            return False
        return True

    @action(
        description="Mark selected orders paid and provision", permissions=["change"]
    )
    def mark_paid(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        done = sum(self._mark_paid(request, order.pk, "") for order in queryset)
        self.message_user(request, f"{done} order(s) marked paid.")

    @action(description="Cancel selected pending orders", permissions=["change"])
    def cancel_orders(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        done = sum(self._cancel(request, order.pk) for order in queryset)
        self.message_user(request, f"{done} order(s) cancelled.")

    @action(
        description=_("Mark paid"),
        icon="paid",
        permissions=["change"],
        url_path="mark-paid",
        dialog=confirm_dialog(
            _("Confirm offline payment"),
            _("Marks the order paid and provisions what it contains, once."),
            MarkPaidForm,
            submit=_("Mark paid"),
        ),
    )
    def mark_order_paid(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        order = self.get_object(request, unquote(object_id))
        if order is None:
            raise Http404
        if self._mark_paid(request, order.pk, str(form.cleaned_data["reference"])):
            self.message_user(request, _("Order marked paid and provisioned."))
        return htmx_redirect(return_url(request, change_url(self, order.pk)))

    @action(
        description=_("Cancel order"),
        icon="cancel",
        permissions=["change"],
        url_path="cancel-order",
        dialog=confirm_dialog(
            _("Cancel this order"),
            _("Only pending orders can be cancelled."),
            submit=_("Cancel order"),
        ),
    )
    def cancel_order(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        order = self.get_object(request, unquote(object_id))
        if order is None:
            raise Http404
        if self._cancel(request, order.pk):
            self.message_user(request, _("Order cancelled."))
        return htmx_redirect(return_url(request, change_url(self, order.pk)))
