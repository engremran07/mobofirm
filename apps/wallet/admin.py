"""Credits economy administration (Unfold): wallets, rules, prices, requests, ledger.

Every balance-changing action goes through ``apps.wallet.services`` so permission
checks, idempotency and the append-only ledger apply to staff exactly as to code.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, ClassVar

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.db.models import Model as DjangoModel
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminIntegerFieldWidget, UnfoldAdminTextInputWidget

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    ReasonForm,
    SingletonModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.users.request_user import current_user

from . import registry, services
from .models import (
    CreditPrice,
    EscrowHold,
    LedgerEntry,
    PayoutRequest,
    RewardRefusal,
    RewardRule,
    TopUpRequest,
    Wallet,
    WalletSettings,
)

logger = logging.getLogger(__name__)

STATUS_BADGES = {
    "pending": "warning",
    "approved": "info",
    "paid": "success",
    "rejected": "danger",
    "cancelled": "danger",
    "holding": "warning",
    "released": "success",
}


class AdjustForm(BaseDialogForm):
    credits = forms.IntegerField(
        label=_("Credits"),
        help_text=_("Positive adds credits, negative removes them."),
        widget=UnfoldAdminIntegerFieldWidget,
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        help_text=_("Shown to the user and kept in the ledger."),
        widget=UnfoldAdminTextInputWidget,
    )

    def clean_credits(self) -> int:
        value = int(self.cleaned_data["credits"])
        if value == 0:
            raise forms.ValidationError(_("Enter a non-zero number of credits."))
        return value


class ReferenceForm(BaseDialogForm):
    reference = forms.CharField(
        label=_("Payment reference"),
        required=False,
        max_length=200,
        help_text=_("Receipt or transfer reference kept on the request."),
        widget=UnfoldAdminTextInputWidget,
    )


class WalletLedgerInline(TabularInline):
    model = LedgerEntry
    fields = ("created_at", "direction", "reason", "amount", "balance_after", "note")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    tab = True
    ordering = ("-id",)
    verbose_name_plural = _("Ledger")

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


class WalletEscrowInline(TabularInline):
    model = EscrowHold
    fields = ("created_at", "credits", "status", "reference")
    readonly_fields = fields
    can_delete = False
    tab = True
    ordering = ("-id",)
    verbose_name_plural = _("Escrow holds")

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


class WalletPayoutInline(TabularInline):
    model = PayoutRequest
    fields = ("created_at", "credits", "payout_amount", "method", "status")
    readonly_fields = fields
    can_delete = False
    show_change_link = True
    tab = True
    ordering = ("-id",)
    verbose_name_plural = _("Payout requests")

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(WalletSettings)
class WalletSettingsAdmin(SingletonModelAdmin):
    """Credits & pricing: the one place that defines what a dollar is worth in credits."""

    readonly_fields = ("rate_warning", "rate_preview")
    fieldsets = (
        (
            _("Credits & pricing"),
            {
                "classes": ["tab"],
                "description": _(
                    "Prices in the shop and marketplace are set in dollars; credits are "
                    "always derived as price × rate."
                ),
                "fields": (
                    "currency_label",
                    "credits_per_currency",
                    "rate_warning",
                    "rate_preview",
                ),
            },
        ),
        (
            _("Top-ups"),
            {
                "classes": ["tab"],
                "fields": (
                    "topups_enabled",
                    "min_topup_credits",
                    "max_topup_credits",
                    "max_open_topups",
                ),
            },
        ),
        (
            _("Payouts"),
            {
                "classes": ["tab"],
                "fields": (
                    "payouts_enabled",
                    "min_payout_credits",
                    "payout_fee_percent",
                ),
            },
        ),
    )

    @display(description=_("Before you change the rate"))
    def rate_warning(self, _obj: WalletSettings | None) -> str:
        return str(
            _(
                "Existing prices are derived, so changing the rate re-prices every "
                "package, paid file and listing immediately. Credits already spent, "
                "earned or refunded stay exactly as recorded: the ledger is never "
                "rewritten."
            )
        )

    @display(description=_("What the rate means"))
    def rate_preview(self, obj: WalletSettings | None) -> SafeString:
        rate = int(obj.credits_per_currency) if obj and obj.pk else 10
        rows = [
            (Decimal(amount), services.to_credits_at(Decimal(amount), rate))
            for amount in ("0.50", "1", "4", "9", "29", "79")
        ]
        return format_html_join(
            "",
            '<p class="mb-1">${} = <strong>{} credits</strong></p>',
            ((f"{amount:.2f}", credits) for amount, credits in rows),
        )

    def save_model(
        self, request: HttpRequest, obj: WalletSettings, form: Any, change: bool
    ) -> None:
        old_rate = (
            WalletSettings.objects.filter(pk=obj.pk)
            .values_list("credits_per_currency", flat=True)
            .first()
        )
        super().save_model(request, obj, form, change)
        if old_rate is not None and old_rate != obj.credits_per_currency:
            logger.warning(
                "Credit rate changed %s -> %s per %s by user %s",
                old_rate,
                obj.credits_per_currency,
                obj.currency_label,
                request.user.pk,
            )
            self.message_user(
                request,
                _(
                    "The rate changed from %(old)s to %(new)s credits per %(currency)s. "
                    "Prices were re-derived; the ledger was not rewritten."
                )
                % {
                    "old": old_rate,
                    "new": obj.credits_per_currency,
                    "currency": obj.currency_label,
                },
                messages.WARNING,
            )


@register(Wallet)
class WalletAdmin(NoAddModelAdmin):
    list_display = (
        "user",
        "balance",
        "held",
        "available_credits",
        "frozen_badge",
        "updated_at",
    )
    list_filter = (("updated_at", RangeDateTimeFilter), "is_frozen")
    search_fields = ("user__email", "user__username")
    list_select_related = ("user",)
    readonly_fields = (
        "user",
        "balance",
        "held",
        "available_credits",
        "ledger_check",
        "is_frozen",
        "frozen_reason",
        "created_at",
        "updated_at",
    )
    fields = readonly_fields
    inlines = (WalletLedgerInline, WalletEscrowInline, WalletPayoutInline)
    actions_detail = ("adjust_balance", "freeze_wallet", "unfreeze_wallet")

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False

    @display(description=_("Available"))
    def available_credits(self, obj: Wallet) -> int:
        return obj.available

    @display(description=_("Status"), label={True: "danger", False: "success"})
    def frozen_badge(self, obj: Wallet) -> tuple[bool, str]:
        return obj.is_frozen, "Frozen" if obj.is_frozen else "Active"

    @display(description=_("Ledger check"))
    def ledger_check(self, obj: Wallet) -> str:
        audit = services.audit_wallet(obj)
        if audit.consistent:
            return f"Consistent ({audit.entries} entries)"
        return format_html(
            "MISMATCH: ledger says {}, wallet says {}", audit.expected, audit.actual
        )

    def _wallet(self, request: HttpRequest, object_id: str) -> Wallet:
        wallet = self.get_object(request, unquote(object_id))
        if wallet is None:
            raise Http404
        return wallet

    @action(
        description=_("Adjust credits"),
        icon="tune",
        permissions=["adjust"],
        url_path="adjust-credits",
        dialog=confirm_dialog(
            _("Adjust credits"),
            _("Adds or removes credits with a written reason, kept in the ledger."),
            AdjustForm,
            submit=_("Apply adjustment"),
        ),
    )
    def adjust_balance(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        wallet = self._wallet(request, object_id)
        try:
            services.adjust(
                wallet.user,
                form.cleaned_data["credits"],
                actor=current_user(request),
                reason_text=str(form.cleaned_data["reason"]),
                key=f"adjust:{uuid.uuid4().hex}",
            )
        except (services.WalletError, PermissionDenied) as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Balance adjusted."))
        return htmx_redirect(return_url(request, change_url(self, wallet.pk)))

    @action(
        description=_("Freeze"),
        icon="lock",
        permissions=["adjust"],
        url_path="freeze-wallet",
        dialog=confirm_dialog(
            _("Freeze this wallet"),
            _("Blocks earning and spending until it is unfrozen."),
            ReasonForm,
            submit=_("Freeze wallet"),
        ),
    )
    def freeze_wallet(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        wallet = self._wallet(request, object_id)
        services.set_frozen(
            wallet.user,
            True,
            actor=current_user(request),
            reason_text=str(form.cleaned_data["reason"]),
        )
        self.message_user(request, _("Wallet frozen."))
        return htmx_redirect(return_url(request, change_url(self, wallet.pk)))

    @action(
        description=_("Unfreeze"),
        icon="lock_open",
        permissions=["adjust"],
        url_path="unfreeze-wallet",
        dialog=confirm_dialog(_("Unfreeze this wallet"), submit=_("Unfreeze wallet")),
    )
    def unfreeze_wallet(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        wallet = self._wallet(request, object_id)
        services.set_frozen(
            wallet.user,
            False,
            actor=current_user(request),
            reason_text="",
        )
        self.message_user(request, _("Wallet unfrozen."))
        return htmx_redirect(return_url(request, change_url(self, wallet.pk)))

    def has_adjust_permission(
        self, request: HttpRequest, _object_id: str | None = None
    ) -> bool:
        return request.user.has_perm("wallet.adjust_wallet")


@register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "wallet",
        "direction_badge",
        "reason",
        "amount",
        "balance_after",
        "event_key",
        "created_at",
    )
    list_filter = (
        ("direction", ChoicesDropdownFilter),
        ("reason", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("key", "reference", "event_key", "wallet__user__email")
    list_select_related = ("wallet__user",)
    ordering = ("-id",)

    @display(
        description=_("Direction"),
        ordering="direction",
        label={"credit": "success", "debit": "warning"},
    )
    def direction_badge(self, obj: LedgerEntry) -> str:
        return obj.direction


@register(RewardRule)
class RewardRuleAdmin(ModelAdmin):
    list_display = (
        "label",
        "event_key",
        "app",
        "enabled",
        "credits",
        "daily_cap",
        "monthly_cap",
        "lifetime_cap",
    )
    list_editable = ("enabled", "credits")
    list_filter = ("app", "enabled")
    search_fields = ("event_key", "label")
    readonly_fields = ("event_key", "label", "app", "dynamic_amount", "updated_at")
    fieldsets = (
        (
            _("Reward"),
            {
                "classes": ["tab"],
                "fields": ("label", "event_key", "app", "enabled", "credits"),
            },
        ),
        (
            _("Caps and pacing"),
            {
                "classes": ["tab"],
                "fields": (
                    "daily_cap",
                    "monthly_cap",
                    "lifetime_cap",
                    "cooldown_seconds",
                ),
            },
        ),
        (
            _("Eligibility"),
            {
                "classes": ["tab"],
                "fields": (
                    "requires_verified_email",
                    "min_account_age_days",
                    "per_object_once",
                    "dynamic_amount",
                    "updated_at",
                ),
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(CreditPrice)
class CreditPriceAdmin(ModelAdmin):
    list_display = ("label", "sink_key", "app", "price_display", "enabled")
    list_editable = ("enabled",)
    list_filter = ("app", "enabled")
    search_fields = ("sink_key", "label")
    readonly_fields = ("sink_key", "label", "app", "price_source", "updated_at")
    fields = (
        "label",
        "sink_key",
        "app",
        "price_source",
        "credits",
        "enabled",
        "updated_at",
    )

    @staticmethod
    def _is_derived(obj: CreditPrice | None) -> bool:
        sink = registry.spend_sinks().get(obj.sink_key) if obj else None
        return bool(sink and sink.derived)

    @display(description=_("Price"))
    def price_display(self, obj: CreditPrice) -> str:
        if self._is_derived(obj):
            return str(_("Derived: dollars × rate"))
        return f"{obj.credits} credits"

    @display(description=_("Where the price comes from"))
    def price_source(self, obj: CreditPrice | None) -> str:
        if self._is_derived(obj):
            return str(
                _(
                    "Set on each package or file in dollars and converted with the "
                    "rate under Credits & pricing; nothing to edit here."
                )
            )
        sink = registry.spend_sinks().get(obj.sink_key) if obj else None
        if sink is not None and sink.user_price is not None:
            return str(
                _(
                    "A fixed number of credits, edited below. This is the global price: "
                    "a package can set its own (Shop → Packages), and prices shown to "
                    "users already follow that."
                )
            )
        return str(_("A fixed number of credits, edited below."))

    def get_readonly_fields(
        self, request: HttpRequest, obj: CreditPrice | None = None
    ) -> tuple[str, ...]:
        fields = tuple(super().get_readonly_fields(request, obj))
        return (*fields, "credits") if self._is_derived(obj) else fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(RewardRefusal)
class RewardRefusalAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "user", "event_key", "reason", "credits", "detail")
    list_filter = ("reason", "event_key", ("created_at", RangeDateTimeFilter))
    search_fields = ("key", "user__email", "event_key")
    list_select_related = ("user",)


@register(EscrowHold)
class EscrowHoldAdmin(ReadOnlyModelAdmin):
    list_display = (
        "id",
        "wallet",
        "credits",
        "status_badge",
        "reference",
        "created_at",
    )
    list_filter = (("status", ChoicesDropdownFilter),)
    search_fields = ("key", "reference", "wallet__user__email")
    list_select_related = ("wallet__user",)

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: EscrowHold) -> str:
        return obj.status


@register(TopUpRequest)
class TopUpRequestAdmin(NoAddModelAdmin):
    list_display = ("id", "user", "credits", "price", "status_badge", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "order_reference", "payment_reference")
    list_select_related = ("user",)
    readonly_fields = (
        "user",
        "credits",
        "price",
        "currency_label",
        "status",
        "order_reference",
        "payment_reference",
        "resolved_by",
        "resolved_at",
        "created_at",
    )
    fields = readonly_fields
    actions_row: ClassVar[tuple[str, ...]] = ("confirm_topup", "cancel_topup")
    actions_detail: ClassVar[tuple[str, ...]] = ("confirm_topup", "cancel_topup")

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: TopUpRequest) -> str:
        return obj.status

    def _target(self, request: HttpRequest, object_id: str) -> TopUpRequest:
        obj = self.get_object(request, unquote(object_id))
        if obj is None:
            raise Http404
        return obj

    @action(
        description=_("Confirm payment"),
        icon="paid",
        permissions=["change"],
        url_path="confirm-topup",
        dialog=confirm_dialog(
            _("Confirm offline payment"),
            _("Grants the credits to the user, once."),
            ReferenceForm,
            submit=_("Grant credits"),
        ),
    )
    def confirm_topup(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        target = self._target(request, object_id)
        try:
            services.confirm_topup(
                target.pk,
                staff_user=current_user(request),
                reference=str(form.cleaned_data["reference"]),
            )
        except (services.WalletError, PermissionDenied) as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Top-up confirmed and credits granted."))
        return htmx_redirect(return_url(request, change_url(self, target.pk)))

    @action(
        description=_("Cancel"),
        icon="cancel",
        permissions=["change"],
        url_path="cancel-topup",
        dialog=confirm_dialog(_("Cancel this top-up"), submit=_("Cancel top-up")),
    )
    def cancel_topup(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        target = self._target(request, object_id)
        try:
            services.cancel_topup(target.pk, staff_user=current_user(request))
        except (services.WalletError, PermissionDenied) as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Top-up cancelled."))
        return htmx_redirect(return_url(request, change_url(self, target.pk)))


@register(PayoutRequest)
class PayoutRequestAdmin(NoAddModelAdmin):
    list_display = (
        "id",
        "wallet",
        "credits",
        "payout_amount",
        "method",
        "status_badge",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("method", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("wallet__user__email", "destination", "payment_reference")
    list_select_related = ("wallet__user",)
    readonly_fields = (
        "wallet",
        "credits",
        "payout_amount",
        "currency_label",
        "fee_percent",
        "method",
        "destination",
        "status",
        "decision_reason",
        "payment_reference",
        "reviewed_by",
        "reviewed_at",
        "paid_at",
        "created_at",
    )
    fields = readonly_fields
    actions_row: ClassVar[tuple[str, ...]] = (
        "approve_request",
        "mark_request_paid",
        "reject_request",
    )
    actions_detail: ClassVar[tuple[str, ...]] = actions_row

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: PayoutRequest) -> str:
        return obj.status

    def _run(
        self,
        request: HttpRequest,
        object_id: str,
        done: str,
        operation: str,
        **extra: str,
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        try:
            getattr(services, operation)(
                target.pk,
                staff_user=current_user(request),
                **extra,
            )
        except (services.WalletError, PermissionDenied) as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, done)
        return htmx_redirect(return_url(request, change_url(self, target.pk)))

    @action(
        description=_("Approve"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-payout",
        dialog=confirm_dialog(
            _("Approve this payout"),
            _("Confirms the request is legitimate; pay it, then mark it paid."),
            submit=_("Approve"),
        ),
    )
    def approve_request(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(request, object_id, "Payout approved.", "approve_payout")

    @action(
        description=_("Mark paid"),
        icon="paid",
        permissions=["change"],
        url_path="mark-payout-paid",
        dialog=confirm_dialog(
            _("Mark payout paid"),
            _("Records that the money was sent."),
            ReferenceForm,
            submit=_("Mark paid"),
        ),
    )
    def mark_request_paid(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(
            request,
            object_id,
            "Payout marked paid.",
            "mark_payout_paid",
            reference=str(form.cleaned_data["reference"]),
        )

    @action(
        description=_("Reject"),
        icon="cancel",
        permissions=["change"],
        url_path="reject-payout",
        dialog=confirm_dialog(
            _("Reject this payout"),
            _("The held credits go back to the user, with your reason."),
            ReasonForm,
            submit=_("Reject payout"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(
            request,
            object_id,
            "Payout rejected and credits returned.",
            "reject_payout",
            reason_text=str(form.cleaned_data["reason"]),
        )
