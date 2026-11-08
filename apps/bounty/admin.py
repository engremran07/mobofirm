"""Bounty administration (Unfold): bounties with their claims, log and disputes."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import Count, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.forms import BaseDialogForm

from apps.bounty import services
from apps.bounty.models import (
    Bounty,
    BountyClaim,
    BountyDispute,
    BountyLog,
)
from apps.core.admin_base import (
    ConfirmForm,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    ReasonForm,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.users.request_user import current_user

STATUS_BADGES = {
    "open": "success",
    "fulfilled": "info",
    "review": "warning",
    "cancelled": "danger",
    "expired": "danger",
}


class ClaimInline(TabularInline):
    model = BountyClaim
    fields = ("user", "note", "created_at")
    readonly_fields = ("user", "note", "created_at")
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: Bounty | None = None
    ) -> bool:
        return False


class LogInline(TabularInline):
    model = BountyLog
    fields = ("created_at", "kind", "actor", "detail")
    readonly_fields = ("created_at", "kind", "actor", "detail")
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: Bounty | None = None
    ) -> bool:
        return False


class DisputeInline(TabularInline):
    model = BountyDispute
    fields = ("reporter", "reason", "status", "outcome")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(
        self, request: HttpRequest, obj: Bounty | None = None
    ) -> bool:
        return False


@register(Bounty)
class BountyAdmin(NoAddModelAdmin):
    """Bounties are posted by members (their credits go into escrow), never by staff."""

    list_display = (
        "id",
        "target_label",
        "credits",
        "status_badge",
        "creator",
        "claim_total",
        "expires_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("target_label", "creator__username", "creator__email")
    list_select_related = ("creator",)
    inlines = (ClaimInline, DisputeInline, LogInline)
    actions_row = ("cancel_refund", "reopen")
    actions_detail = ("cancel_refund", "reopen")
    actions_list = ("expire_now",)
    readonly_fields = (
        "creator",
        "target_label",
        "target_type",
        "target_id",
        "credits",
        "status",
        "escrow_hold_id",
        "expires_at",
        "created_at",
        "settled_at",
        "settled_to",
        "settled_firmware",
        "review_reason",
        "closed_reason",
    )
    fieldsets = (
        (
            _("Bounty"),
            {
                "fields": (
                    "creator",
                    "target_label",
                    "credits",
                    "status",
                    "note",
                    "expires_at",
                    "created_at",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Settlement"),
            {
                "fields": (
                    "settled_to",
                    "settled_at",
                    "settled_firmware",
                    "review_reason",
                    "closed_reason",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Escrow & target"),
            {
                "fields": ("escrow_hold_id", "target_type", "target_id"),
                "classes": ["tab"],
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Bounty]:
        return super().get_queryset(request).annotate(claims_n=Count("claims"))

    def has_delete_permission(
        self, request: HttpRequest, obj: Bounty | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), label=STATUS_BADGES, ordering="status")
    def status_badge(self, obj: Bounty) -> str:
        return obj.status

    @display(description=_("Working on it"), ordering="claims_n")
    def claim_total(self, obj: Bounty) -> int:
        return int(getattr(obj, "claims_n", 0))

    @action(
        description=_("Cancel and refund"),
        icon="undo",
        permissions=["change"],
        url_path="cancel-refund",
        dialog=confirm_dialog(
            _("Cancel this bounty"),
            _("The poster's escrowed credits are returned in full."),
            ReasonForm,
            submit=_("Cancel and refund"),
        ),
    )
    def cancel_refund(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        bounty = self.get_object(request, unquote(object_id))
        if bounty is None:
            raise Http404
        try:
            services.cancel(
                bounty, actor=request.user, reason=str(form.cleaned_data["reason"])
            )
        except services.BountyError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Bounty cancelled; credits returned."))
        return htmx_redirect(return_url(request, change_url(self, bounty.pk)))

    @action(
        description=_("Send back to open"),
        icon="replay",
        permissions=["change"],
        url_path="reopen",
        dialog=confirm_dialog(
            _("Reopen this bounty"),
            _("It becomes available to the next matching approved upload."),
            ConfirmForm,
            submit=_("Reopen"),
        ),
    )
    def reopen(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        bounty = self.get_object(request, unquote(object_id))
        if bounty is None:
            raise Http404
        if bounty.status == Bounty.Status.REVIEW:
            bounty.status = Bounty.Status.OPEN
            bounty.review_reason = ""
            bounty.save(update_fields=["status", "review_reason", "updated_at"])
            BountyLog.objects.create(
                bounty=bounty, kind="reopened", actor=current_user(request)
            )
            self.message_user(request, _("Bounty reopened."))
        else:
            self.message_user(
                request, _("Only bounties in review can be reopened."), messages.ERROR
            )
        return htmx_redirect(return_url(request, change_url(self, bounty.pk)))

    @action(description=_("Expire overdue bounties now"), icon="schedule")
    def expire_now(self, request: HttpRequest) -> HttpResponse:
        count = services.expire_due()
        self.message_user(
            request, _("%(count)d bounty(ies) expired and refunded.") % {"count": count}
        )
        return redirect("admin:bounty_bounty_changelist")


class ResolveReasonForm(ReasonForm):
    """Why staff decided this way; shown to the reporter's audit trail."""


@register(BountyDispute)
class BountyDisputeAdmin(NoAddModelAdmin):
    """The staff review queue: set the payee, then pay, refund or dismiss."""

    list_display = ("id", "bounty", "reporter", "status_badge", "outcome", "created_at")
    list_filter = (("status", ChoicesDropdownFilter),)
    search_fields = ("bounty__target_label", "reporter__username", "reason")
    list_select_related = ("bounty", "reporter")
    raw_id_fields = ("payee",)
    actions_row = ("refund_poster", "pay_payee", "dismiss")
    actions_detail = ("refund_poster", "pay_payee", "dismiss")
    readonly_fields = (
        "bounty",
        "reporter",
        "reason",
        "status",
        "outcome",
        "resolution_note",
        "resolved_by",
        "resolved_at",
        "created_at",
    )
    fields = (
        "bounty",
        "reporter",
        "reason",
        "status",
        "payee",
        "outcome",
        "resolution_note",
        "resolved_by",
        "resolved_at",
        "created_at",
    )

    def has_delete_permission(
        self, request: HttpRequest, obj: BountyDispute | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), label={"open": "warning", "resolved": "success"})
    def status_badge(self, obj: BountyDispute) -> str:
        return obj.status

    def _resolve(
        self,
        request: HttpRequest,
        form: BaseDialogForm,
        object_id: str,
        outcome: str,
    ) -> HttpResponse:
        dispute = self.get_object(request, unquote(object_id))
        if dispute is None:
            raise Http404
        try:
            services.resolve_dispute(
                dispute,
                outcome=outcome,
                staff=request.user,
                note=str(form.cleaned_data["reason"]),
                payee=dispute.payee,
            )
        except services.BountyError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Report resolved."))
        return htmx_redirect(return_url(request, change_url(self, dispute.pk)))

    @action(
        description=_("Refund the poster"),
        icon="undo",
        permissions=["change"],
        url_path="refund-poster",
        dialog=confirm_dialog(
            _("Refund the poster"),
            _("Cancels the bounty and returns the escrowed credits."),
            ResolveReasonForm,
            submit=_("Refund"),
        ),
    )
    def refund_poster(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._resolve(request, form, object_id, BountyDispute.Outcome.REFUNDED)

    @action(
        description=_("Pay the payee"),
        icon="payments",
        permissions=["change"],
        url_path="pay-payee",
        dialog=confirm_dialog(
            _("Pay the payee"),
            _("Pays the escrowed credits to the payee set on this report."),
            ResolveReasonForm,
            submit=_("Pay"),
        ),
    )
    def pay_payee(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._resolve(request, form, object_id, BountyDispute.Outcome.PAID)

    @action(
        description=_("Dismiss"),
        icon="close",
        permissions=["change"],
        url_path="dismiss-report",
        dialog=confirm_dialog(
            _("Dismiss this report"),
            _("The bounty stays as it is."),
            ResolveReasonForm,
            submit=_("Dismiss"),
        ),
    )
    def dismiss(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._resolve(request, form, object_id, BountyDispute.Outcome.DISMISSED)


@register(BountyLog)
class BountyLogAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "bounty", "kind", "actor", "detail")
    list_filter = ("kind", ("created_at", RangeDateTimeFilter))
    search_fields = ("bounty__target_label", "detail", "actor__username")
    list_select_related = ("bounty", "actor")
