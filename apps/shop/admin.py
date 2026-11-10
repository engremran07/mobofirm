"""Shop administration (Unfold): packages, user packages and offline orders.

A package is edited on ONE form with tabs (Basic, Bandwidth, Item types, Extra, Fair
usage, Perks, Preview); lists and the preview show the same human ``summary()`` the
public pricing page uses. Online payments are disabled by owner decision, so orders
are confirmed by staff after offline payment. State changes always go through
``apps.shop.services`` so provisioning stays exactly-once and permission-checked.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, ClassVar

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, QuerySet
from django.db.models import Model as DjangoModel
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import URLPattern, path, reverse
from django.utils.html import escape, format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    SingletonModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import overrides, services, wallet_gateway
from .dialogs import (
    ArchivePackageForm,
    CancelOrderForm,
    CancelPackageForm,
    DuplicatePackageForm,
    MarkPaidForm,
    RefundOrderForm,
)
from .models import (
    FeaturedBlogPurchase,
    Order,
    OrderItem,
    PackageQuotaOverride,
    PackageTier,
    QuotaConsumption,
    QuotaOverrideAudit,
    ShopSettings,
    UserPackage,
    UserQuotaOverride,
)

MAX_PRICE = Decimal("99999999.99")

STATUS_BADGES = {
    "pending": "warning",
    "paid": "success",
    "refunded": "info",
    "cancelled": "danger",
    "active": "success",
    "expired": "warning",
    "suspended": "danger",
}
PACKAGE_STATE_BADGES = {
    "Inactive": "danger",
    "Default": "info",
    "Public": "success",
    "Hidden": "warning",
}


class UserPackageInline(TabularInline):
    """Who holds this package; granting and cancelling happen on the user package."""

    model = UserPackage
    tab = True
    verbose_name_plural = _("Holders")
    fields = ("user", "status", "source", "started_at", "expires_at")
    readonly_fields = fields
    ordering = ("-started_at",)
    max_num = 0
    can_delete = False
    show_change_link = True

    def get_queryset(self, request: HttpRequest) -> QuerySet[UserPackage]:
        """Purchased and granted holders; the free baseline rows (everyone) are omitted."""
        return super().get_queryset(request).filter(is_baseline=False)

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(PackageTier)
class PackageTierAdmin(ModelAdmin):
    list_display = (
        "name",
        "summary_display",
        "state_badge",
        "holders",
        "sort_order",
    )
    list_filter = (
        "is_active",
        "is_public",
        "is_default",
    )
    list_editable = ("sort_order",)
    list_display_links = ("name",)
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    search_fields = ("name", "slug", "description")
    readonly_fields = (
        "credits_equivalent",
        "summary_preview",
        "created_at",
        "updated_at",
    )
    inlines = (UserPackageInline,)
    actions = ("activate_selected", "deactivate_selected")
    actions_row = ("duplicate_package", "archive_package")
    actions_detail = ("duplicate_package", "archive_package")
    fieldsets = (
        (
            _("Basic"),
            {
                "fields": (
                    "name",
                    "slug",
                    "description",
                    ("price", "allow_credits", "credit_price_override"),
                    "credits_equivalent",
                    "duration_days",
                    ("limit_devices", "device_limit"),
                    "slot_reset_credits",
                    ("is_public", "is_active"),
                    ("is_default", "is_popular"),
                    "sort_order",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Bandwidth"),
            {
                "fields": (
                    "bandwidth_enabled",
                    ("limit_files", "limit_size", "size_unit"),
                    "window",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Item types"),
            {
                "fields": (
                    "applies_to_free",
                    "applies_to_featured",
                    "applies_to_paid",
                    "paid_discount_percent",
                    ("applies_to_featured_blog", "featured_blog_limit"),
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Extra"),
            {
                "fields": ("extra_daily_files", "extra_daily_size"),
                "classes": ["tab"],
            },
        ),
        (
            _("Fair usage"),
            {
                "fields": (
                    ("fair_daily_files", "fair_daily_size"),
                    ("fair_monthly_files", "fair_monthly_size"),
                    "max_concurrent_downloads",
                    "cooldown_seconds",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Perks"),
            {
                "fields": (
                    "ad_free",
                    "priority_queue",
                    "resume_support",
                    "direct_links",
                    "early_access",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Preview"),
            {
                "fields": ("summary_preview", "created_at", "updated_at"),
                "classes": ["tab"],
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[PackageTier]:
        return (
            super()
            .get_queryset(request)
            .annotate(
                live_holders=Count(
                    "user_packages",
                    filter=Q(user_packages__status=UserPackage.Status.ACTIVE),
                )
            )
        )

    @display(description=_("Credits equivalent"))
    def credits_equivalent(self, obj: PackageTier | None) -> SafeString:
        """``$9.00 · 90 credits`` for the saved price; the price field refreshes it."""
        text = (
            obj.price_with_credits
            if obj is not None and obj.pk
            else _("Enter a price to see its credits.")
        )
        return format_html('<span id="mf-credits-equivalent">{}</span>', text)

    def formfield_for_dbfield(
        self, db_field: Any, request: HttpRequest, **kwargs: Any
    ) -> Any:
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if field is not None and db_field.name == "price":
            field.widget.attrs.update(
                {
                    "hx-get": reverse("admin:shop_packagetier_credits_preview"),
                    "hx-trigger": "input changed delay:300ms",
                    "hx-target": "#mf-credits-equivalent",
                    "hx-swap": "innerHTML",
                }
            )
        return field

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "credits-preview/",
                self.admin_site.admin_view(self.credits_preview),
                name="shop_packagetier_credits_preview",
            ),
            *super().get_urls(),
        ]

    def credits_preview(self, request: HttpRequest) -> HttpResponse:
        """The ``$ · credits`` text for the price being typed (HTMX, read-only)."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        try:
            amount = Decimal(request.GET.get("price", "").strip())
        except InvalidOperation:
            return HttpResponse(str(_("Enter a valid price.")))
        if not amount.is_finite() or amount < 0 or amount > MAX_PRICE:
            return HttpResponse(str(_("Enter a valid price.")))
        if amount == 0:
            return HttpResponse(str(_("Free")))
        return HttpResponse(escape(wallet_gateway.price_label(amount)))

    @display(description=_("Summary"))
    def summary_display(self, obj: PackageTier) -> str:
        return obj.summary()

    @display(description=_("State"), label=PACKAGE_STATE_BADGES)
    def state_badge(self, obj: PackageTier) -> str:
        if not obj.is_active:
            return "Inactive"
        if obj.is_default:
            return "Default"
        return "Public" if obj.is_public else "Hidden"

    @display(description=_("Holders"), ordering="live_holders")
    def holders(self, obj: Any) -> int:
        return int(getattr(obj, "live_holders", 0))

    @display(description=_("How it reads"))
    def summary_preview(self, obj: PackageTier | None) -> SafeString | str:
        if obj is None or obj.pk is None:
            return format_html("{}", _("Save the package to see its summary."))
        rows = [
            (_("Summary"), obj.summary()),
            (_("Quota"), obj.quota_text),
            (_("Extra"), obj.extra_text or "—"),
            (_("Item types"), obj.types_text),
            (_("Devices"), obj.devices_text),
            (_("Duration"), obj.duration_text),
            (_("Price"), obj.price_text),
            (_("Perks"), ", ".join(obj.perk_labels) or "—"),
        ]
        return format_html_join(
            "",
            '<p class="mb-1"><strong>{}:</strong> {}</p>',
            rows,
        )

    @action(description=_("Activate selected packages"), permissions=["change"])
    def activate_selected(
        self, request: HttpRequest, queryset: QuerySet[PackageTier]
    ) -> None:
        count = 0
        for package in queryset:
            services.set_package_active(package, active=True)
            count += 1
        self.message_user(
            request, _("%(count)d package(s) activated.") % {"count": count}
        )

    @action(description=_("Deactivate selected packages"), permissions=["change"])
    def deactivate_selected(
        self, request: HttpRequest, queryset: QuerySet[PackageTier]
    ) -> None:
        count = 0
        for package in queryset:
            try:
                services.set_package_active(package, active=False)
            except services.OrderError as exc:
                self.message_user(request, f"{package.name}: {exc}", messages.ERROR)
            else:
                count += 1
        self.message_user(
            request, _("%(count)d package(s) deactivated.") % {"count": count}
        )

    def has_duplicate_permission(
        self, request: HttpRequest, _object_id: str | None = None
    ) -> bool:
        return self.has_add_permission(request)

    @action(
        description=_("Duplicate"),
        icon="content_copy",
        permissions=["duplicate"],
        url_path="duplicate-package",
        dialog=confirm_dialog(
            _("Duplicate this package"),
            _("Creates an inactive, hidden copy you can edit before publishing."),
            DuplicatePackageForm,
            submit=_("Duplicate"),
        ),
    )
    def duplicate_package(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        package = self.get_object(request, unquote(object_id))
        if package is None:
            raise Http404
        copy = services.duplicate_package(package)
        self.message_user(request, _("Created the draft “%(name)s”.") % {"name": copy})
        return htmx_redirect(change_url(self, copy.pk))

    @action(
        description=_("Archive"),
        icon="archive",
        permissions=["change"],
        url_path="archive-package",
        dialog=confirm_dialog(
            _("Archive this package"),
            _(
                "Hides the package, stops new orders and cancels its pending orders. "
                "Current holders keep it until it ends."
            ),
            ArchivePackageForm,
            submit=_("Archive"),
        ),
    )
    def archive_package(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        package = self.get_object(request, unquote(object_id))
        if package is None:
            raise Http404
        try:
            services.archive_package(package)
        except services.OrderError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(request, _("Package archived."))
        return htmx_redirect(return_url(request, change_url(self, package.pk)))


class QuotaOverrideInline(TabularInline):
    """Staff exceptions on one user package; empty fields inherit the package."""

    model = PackageQuotaOverride
    tab = True
    verbose_name_plural = _("Quota overrides")
    fields = ("limit_files", "limit_size_mb", "device_limit", "reason", "expires_at")
    ordering = ("-created_at",)


@register(UserPackage)
class UserPackageAdmin(ModelAdmin):
    list_display = (
        "user",
        "package",
        "status_badge",
        "source",
        "is_baseline",
        "started_at",
        "expires_at",
        "order",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("source", ChoicesDropdownFilter),
        "is_baseline",
        ("package", RelatedDropdownFilter),
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user", "order")
    list_select_related = ("user", "package", "order")
    readonly_fields = ("cancelled_at",)
    inlines = (QuotaOverrideInline,)
    actions = ("cancel_selected",)
    actions_row = ("cancel_package",)
    actions_detail = ("cancel_package",)

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, Any]:
        initial = super().get_changeform_initial_data(request)
        initial.setdefault("source", UserPackage.Source.STAFF)
        return initial

    def save_formset(
        self, request: HttpRequest, form: Any, formset: Any, change: bool
    ) -> None:
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, PackageQuotaOverride) and obj.granted_by_id is None:
                obj.granted_by_id = request.user.pk
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: UserPackage) -> str:
        return obj.status

    @action(description=_("Cancel selected packages"), permissions=["change"])
    def cancel_selected(
        self, request: HttpRequest, queryset: QuerySet[UserPackage]
    ) -> None:
        live = queryset.filter(status=UserPackage.Status.ACTIVE, is_baseline=False)
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
            CancelPackageForm,
            submit=_("Cancel package"),
        ),
    )
    def cancel_package(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        package = self.get_object(request, unquote(object_id))
        if package is None:
            raise Http404
        if package.is_baseline:
            self.message_user(
                request, _("The free package cannot be cancelled."), messages.ERROR
            )
        elif package.status == UserPackage.Status.ACTIVE:
            services.cancel_package(package)
            self.message_user(request, _("Package cancelled."))
        else:
            self.message_user(
                request,
                _("Only active packages can be cancelled."),
                messages.ERROR,
            )
        return htmx_redirect(return_url(request, change_url(self, package.pk)))


@register(PackageQuotaOverride)
class PackageQuotaOverrideAdmin(ModelAdmin):
    list_display = ("user_package", "reason", "granted_by", "expires_at")
    list_filter = (("expires_at", RangeDateTimeFilter),)
    search_fields = (
        "user_package__user__email",
        "user_package__user__username",
        "reason",
    )
    raw_id_fields = ("user_package", "granted_by")
    readonly_fields = ("created_at",)

    def save_model(
        self, request: HttpRequest, obj: PackageQuotaOverride, form: Any, change: bool
    ) -> None:
        if not change:
            obj.granted_by_id = request.user.pk
        super().save_model(request, obj, form, change)


@register(ShopSettings)
class ShopSettingsAdmin(SingletonModelAdmin):
    """Shop-wide prices and the download lifetime rules."""

    readonly_fields = ("credits_equivalent",)
    fieldsets = (
        (
            _("Featured files"),
            {
                "fields": ("featured_file_price", "credits_equivalent"),
            },
        ),
        (
            _("Download expiry and history"),
            {
                "description": _(
                    "Free files keep a bounded re-download allowance after the first "
                    "download. Files downloaded under a package leave the active "
                    "library when the package ends and stay in the read-only history "
                    "until the user starts a new package; the history of the old "
                    "package is then purged after the retention below. Files bought "
                    "one by one (cash or credits) never expire."
                ),
                "fields": (
                    "free_redownload_window_days",
                    "free_redownload_max",
                    "package_history_retention_days",
                ),
            },
        ),
    )

    @display(description=_("Credits equivalent"))
    def credits_equivalent(self, obj: ShopSettings | None) -> str:
        price = obj.featured_file_price if obj is not None and obj.pk else None
        if not price:
            return str(_("Not sold one by one."))
        return wallet_gateway.price_label(price)


OVERRIDE_STATE_BADGES = {"Active": "success", "Expired": "warning", "Revoked": "danger"}


@register(UserQuotaOverride)
class UserQuotaOverrideAdmin(ModelAdmin):
    """One account's exception to its package: wins over the package and free default."""

    list_display = (
        "user",
        "summary_display",
        "state_badge",
        "expires_at",
        "granted_by",
    )
    list_filter = ("is_active", ("expires_at", RangeDateTimeFilter))
    search_fields = ("user__email", "user__username", "reason")
    raw_id_fields = ("user", "granted_by")
    readonly_fields = ("summary_preview", "precedence_note", "created_at", "updated_at")
    list_select_related = ("user", "granted_by")
    actions = ("revoke_selected",)
    fieldsets = (
        (
            _("Account"),
            {
                "classes": ["tab"],
                "description": _(
                    "Precedence: this override beats the account's package, which "
                    "beats the free default. Empty fields inherit."
                ),
                "fields": ("user", "reason", "is_active", "precedence_note"),
            },
        ),
        (
            _("Downloads"),
            {
                "classes": ["tab"],
                "fields": (
                    ("limit_files", "limit_size", "size_unit"),
                    "window",
                    ("covers_free", "covers_featured", "covers_paid"),
                ),
            },
        ),
        (
            _("Devices"),
            {"classes": ["tab"], "fields": ("device_limit",)},
        ),
        (
            _("Validity"),
            {
                "classes": ["tab"],
                "fields": (
                    "expires_at",
                    "granted_by",
                    "summary_preview",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @display(description=_("Exception"))
    def summary_display(self, obj: UserQuotaOverride) -> str:
        return obj.summary()

    @display(description=_("State"), label=OVERRIDE_STATE_BADGES)
    def state_badge(self, obj: UserQuotaOverride) -> str:
        if not obj.is_active:
            return "Revoked"
        return "Active" if obj.is_live else "Expired"

    @display(description=_("Precedence"))
    def precedence_note(self, _obj: UserQuotaOverride | None) -> str:
        return str(
            _(
                "Account override → package (and its per-package override) → free "
                "default. Campaign bonuses are added on top."
            )
        )

    @display(description=_("How it reads"))
    def summary_preview(self, obj: UserQuotaOverride | None) -> str:
        if obj is None or obj.pk is None:
            return str(_("Save the override to see its summary."))
        return obj.summary()

    def save_model(
        self, request: HttpRequest, obj: UserQuotaOverride, form: Any, change: bool
    ) -> None:
        if not change and obj.granted_by_id is None:
            obj.granted_by_id = request.user.pk
        overrides.retire_expired(obj.user)
        super().save_model(request, obj, form, change)
        overrides.audit_saved(obj, actor=request.user, created=not change)

    def delete_model(self, request: HttpRequest, obj: UserQuotaOverride) -> None:
        overrides.audit_deleted(obj, actor=request.user)
        super().delete_model(request, obj)

    @action(description=_("Revoke selected overrides"), permissions=["change"])
    def revoke_selected(
        self, request: HttpRequest, queryset: QuerySet[UserQuotaOverride]
    ) -> None:
        count = 0
        for override in queryset.filter(is_active=True):
            overrides.revoke_override(override, actor=request.user)
            count += 1
        self.message_user(
            request, _("%(count)d override(s) revoked.") % {"count": count}
        )


@register(QuotaOverrideAudit)
class QuotaOverrideAuditAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "user", "action", "actor", "reason")
    list_filter = (
        ("action", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "reason")
    list_select_related = ("user", "actor")


class OrderItemInline(TabularInline):
    model = OrderItem
    can_delete = False
    tab = True
    ordering = ("id",)
    fields = ("label", "duration_days", "unit_price", "line_total")
    readonly_fields = fields

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(Order)
class OrderAdmin(NoAddModelAdmin):
    list_display = (
        "id",
        "user",
        "items_summary",
        "status_badge",
        "total",
        "currency",
        "payment_method",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("payment_method", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "payment_reference")
    list_select_related = ("user",)
    readonly_fields = (
        "user",
        "status",
        "total",
        "currency",
        "payment_method",
        "credits_spent",
        "paid_at",
        "fulfilled_at",
        "processed_by",
        "created_at",
        "updated_at",
    )
    inlines = (OrderItemInline,)
    actions = ("mark_paid", "cancel_orders")
    actions_row = ("mark_order_paid", "cancel_order", "refund_paid_order")
    actions_detail = ("mark_order_paid", "cancel_order", "refund_paid_order")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Order]:
        return super().get_queryset(request).prefetch_related("items")

    @display(description=_("Items"))
    def items_summary(self, obj: Order) -> SafeString | str:
        labels = [item.label for item in obj.items.all()]
        return format_html("{}", ", ".join(labels)) if labels else "—"

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
            CancelOrderForm,
            submit=_("Cancel order"),
        ),
    )
    def cancel_order(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        order = self.get_object(request, unquote(object_id))
        if order is None:
            raise Http404
        if self._cancel(request, order.pk):
            self.message_user(request, _("Order cancelled."))
        return htmx_redirect(return_url(request, change_url(self, order.pk)))

    @action(
        description=_("Refund"),
        icon="undo",
        permissions=["change"],
        url_path="refund-order",
        dialog=confirm_dialog(
            _("Refund this order"),
            _(
                "Marks a paid order refunded (return the money offline, or the credits "
                "automatically). Packages it provisioned are cancelled and bought "
                "files can no longer be downloaded."
            ),
            RefundOrderForm,
            submit=_("Refund order"),
        ),
    )
    def refund_paid_order(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        order = self.get_object(request, unquote(object_id))
        if order is None:
            raise Http404
        try:
            services.refund_order(
                order.pk,
                staff_user=request.user,
                reason=str(form.cleaned_data["reason"]),
            )
        except services.OrderError as exc:
            self.message_user(request, f"Order #{order.pk}: {exc}", messages.ERROR)
        else:
            self.message_user(request, _("Order refunded."))
        return htmx_redirect(return_url(request, change_url(self, order.pk)))


@register(QuotaConsumption)
class QuotaConsumptionAdmin(ReadOnlyModelAdmin):
    list_display = (
        "user",
        "reason",
        "size_mb",
        "consumed_at",
        "user_package",
        "object_id",
    )
    list_filter = (
        ("reason", ChoicesDropdownFilter),
        ("consumed_at", RangeDateTimeFilter),
    )
    list_select_related = ("user",)


@register(FeaturedBlogPurchase)
class FeaturedBlogPurchaseAdmin(ReadOnlyModelAdmin):
    """Credit purchases of featured articles; the ledger entry holds the money."""

    list_display = ("user", "post_id", "credits", "created_at")
    list_filter = (("created_at", RangeDateTimeFilter),)
    search_fields = ("user__email", "user__username", "key")
    list_select_related = ("user",)
