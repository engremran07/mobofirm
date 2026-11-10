"""Staff tools for the access type of approved firmware (Unfold admin).

* :class:`AccessChangeAdmin` is the base of the typed firmware admins: the access type
  and price are read-only on the form and change only through the **Change access
  type** dialog (row, detail page) or bulk action, which run through
  :func:`apps.firmwares.access_changes.apply_access_change` (validated, audited,
  notified).
* :class:`AccessChangeRequestAdmin` is the queue of requests from uploaders and
  approved sellers: approve (optionally with another price) or reject with a reason.
* :class:`AccessChangeLogAdmin` is the read-only, append-only history; the latest
  change of a file (typically an automatic promotion) can be reverted there.
* :class:`AutoFeatureSettingsAdmin` edits the automatic popularity promotion and shows
  what it would promote right now.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.forms import ActionForm, BaseDialogForm
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
)

from apps.core.admin_base import (
    BADGE_APPROVAL,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    ReasonForm,
    SingletonModelAdmin,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from . import access_changes, access_requests, auto_feature
from .access_change_models import (
    AccessActor,
    AccessChangeLog,
    AccessChangeRequest,
    AccessChangeSource,
    AutoFeatureSettings,
)
from .access_changes import AccessChange, AccessChangeError
from .models import AccessTier

TIER_BADGES = {"free": "success", "paid": "warning", "featured": "info"}
DIALOG_HELP = _(
    "Free, featured (members with a package) or paid (a price is required). People "
    "who already downloaded the file keep their access: free files keep their "
    "re-download allowance, bought files stay theirs, package files stay until the "
    "package period ends."
)


def _price_field(label: Any, help_text: Any = None) -> forms.DecimalField:
    return forms.DecimalField(
        label=label,
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=UnfoldAdminTextInputWidget,
        help_text=help_text
        or _("USD. Only for paid files; the credits price follows the rate."),
    )


def _check_tier_price(form: forms.Form) -> dict[str, Any]:
    cleaned = form.cleaned_data
    tier, price = cleaned.get("access_tier"), cleaned.get("approved_price")
    if tier == AccessTier.PAID and not price:
        form.add_error("approved_price", _("A paid file needs a price."))
    if tier in (AccessTier.FREE, AccessTier.FEATURED) and price:
        form.add_error("approved_price", _("Only paid files have a price."))
    return cleaned


class AccessChangeFields(forms.Form):
    """The access type, price and reason fields with their cross-field rules."""

    access_tier = forms.ChoiceField(
        choices=AccessTier.choices,
        label=_("New access type"),
        help_text=DIALOG_HELP,
        widget=UnfoldAdminSelectWidget,
    )
    approved_price = _price_field(_("Approved price (USD)"))
    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )

    def clean(self) -> dict[str, Any]:
        """A paid file needs a price; the other types must not carry one."""
        super().clean()
        return _check_tier_price(self)


class AccessChangeForm(AccessChangeFields, BaseDialogForm):
    """The 'Change access type' dialog of one firmware file."""


class AccessChangeActionForm(ActionForm):
    """Inputs of the bulk 'Change access type' action on the changelist."""

    access_tier = forms.ChoiceField(
        choices=[("", "— new access type —"), *AccessTier.choices],
        required=False,
        label=_("Access type (for “Change access type”)"),
        widget=UnfoldAdminSelectWidget,
    )
    approved_price = _price_field(_("Price (USD, paid only)"))
    reason = forms.CharField(
        required=False,
        max_length=1000,
        label=_("Reason (recorded in the history)"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 2}),
    )


class ApproveRequestForm(BaseDialogForm):
    """Approve an access change request, optionally with another price."""

    approved_price = _price_field(
        _("Approved price (USD)"),
        _("Paid requests only. Leave empty to approve the proposed price."),
    )
    note = forms.CharField(
        label=_("Note to the requester"),
        required=False,
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


def tier_label(tier: str, price: Any = None) -> str:
    """``Free`` / ``Featured`` / ``Paid · USD 4.00`` for lists and history."""
    label = str(AccessTier(tier).label)
    return f"{label} · USD {price:.2f}" if price is not None else label


class AccessChangeAdmin(NoAddModelAdmin):
    """Base admin of the typed firmware tables with audited access changes."""

    action_form = AccessChangeActionForm
    actions_row = ("change_access",)
    actions_detail = ("change_access",)

    @display(description=_("Access history"))
    def access_history(self, obj: Any) -> SafeString | str:
        """The last changes of this file and any open request."""
        if obj is None or not obj.pk:
            return "—"
        rows = list(access_changes.history_for(obj).select_related("actor")[:8])
        pending = access_requests.pending_request(obj)
        parts: list[Any] = []
        if pending is not None:
            parts.append(
                format_html(
                    "<p>{}</p>",
                    _("Open request: %(from)s → %(to)s (see Access change requests).")
                    % {
                        "from": tier_label(pending.from_tier),
                        "to": tier_label(pending.to_tier, pending.proposed_price),
                    },
                )
            )
        if not rows:
            parts.append(_("No change since approval."))
        else:
            parts.append(
                format_html(
                    "<ul>{}</ul>",
                    format_html_join(
                        "",
                        "<li>{} · {} → {} · {} · {}</li>",
                        (
                            (
                                row.created_at.strftime("%Y-%m-%d %H:%M"),
                                tier_label(row.from_tier, row.from_price),
                                tier_label(row.to_tier, row.to_price),
                                row.actor or AccessActor(row.actor_kind).label,
                                row.reason or "—",
                            )
                            for row in rows
                        ),
                    ),
                )
            )
        return format_html_join("", "{}", ((part,) for part in parts))

    def _change_one(
        self,
        request: HttpRequest,
        firmware: Any,
        form: Any,
        source: str,
    ) -> bool:
        tier = str(form.cleaned_data["access_tier"])
        try:
            access_changes.apply_access_change(
                firmware,
                AccessChange(
                    to_tier=tier,
                    price=form.cleaned_data.get("approved_price") or None,
                    actor=request.user,
                    source=source,
                    reason=str(form.cleaned_data["reason"]),
                ),
            )
        except AccessChangeError as error:
            self.message_user(
                request, f"{firmware.original_file_name}: {error}", messages.ERROR
            )
            return False
        return True

    @action(
        description=_("Change access type"),
        icon="swap_vert",
        permissions=["change"],
        url_path="change-access",
        dialog=confirm_dialog(
            _("Change the access type"),
            _("Move this approved file between free, featured and paid."),
            AccessChangeForm,
            submit=_("Change"),
        ),
    )
    def change_access(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Dialog action for one file (row and detail page)."""
        firmware = self.get_object(request, unquote(object_id))
        if firmware is None:
            raise Http404
        if self._change_one(request, firmware, form, AccessChangeSource.DIALOG):
            self.message_user(
                request,
                _("%(file)s is now %(tier)s.")
                % {
                    "file": firmware.original_file_name,
                    "tier": tier_label(firmware.access_tier, firmware.approved_price),
                },
            )
        return htmx_redirect(return_url(request, change_url(self, firmware.pk)))

    @action(
        description=_("Change access type of the selected (set it above)"),
        permissions=["change"],
    )
    def change_access_selected(
        self, request: HttpRequest, queryset: QuerySet[Any]
    ) -> None:
        """Bulk change: every file gets the type, price and reason from the form."""
        form = AccessChangeFields(
            {
                "access_tier": request.POST.get("access_tier", ""),
                "approved_price": request.POST.get("approved_price", ""),
                "reason": request.POST.get("reason", ""),
            }
        )
        if not form.is_valid():
            problems = "; ".join(
                str(error) for errors in form.errors.values() for error in errors
            )
            self.message_user(request, problems, messages.ERROR)
            return
        changed = sum(
            self._change_one(request, firmware, form, AccessChangeSource.BULK)
            for firmware in queryset
        )
        self.message_user(
            request,
            _("%(count)d file(s) changed.") % {"count": changed},
        )


@register(AccessChangeRequest)
class AccessChangeRequestAdmin(NoAddModelAdmin):
    """Queue of access change requests from uploaders and approved sellers."""

    actions_row = ("approve_request", "reject_request")
    actions_detail = ("approve_request", "reject_request")
    list_display = (
        "firmware_label",
        "requester",
        "change",
        "status_badge",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("to_tier", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("firmware_label", "requester__email", "requester__username")
    list_select_related = ("requester",)
    readonly_fields = (
        "firmware_label",
        "object_id",
        "content_type",
        "requester",
        "from_tier",
        "to_tier",
        "proposed_price",
        "reason",
        "status",
        "approved_price",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "created_at",
    )
    fields = readonly_fields

    def has_delete_permission(
        self, request: HttpRequest, obj: AccessChangeRequest | None = None
    ) -> bool:
        """Requests are history: they are decided, never deleted."""
        return False

    @display(description=_("Change"))
    def change(self, obj: AccessChangeRequest) -> str:
        """``Free → Paid · USD 4.00``."""
        return (
            f"{tier_label(obj.from_tier)} → "
            f"{tier_label(obj.to_tier, obj.proposed_price)}"
        )

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: AccessChangeRequest) -> str:
        """Colour-coded status."""
        return str(obj.status)

    def _decide(
        self, request: HttpRequest, object_id: str, decide: Any, done: str
    ) -> HttpResponse:
        access_request = self.get_object(request, unquote(object_id))
        if access_request is None:
            raise Http404
        try:
            decide(access_request)
        except AccessChangeError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, done)
        return htmx_redirect(return_url(request, change_url(self, access_request.pk)))

    @action(
        description=_("Approve"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-request",
        dialog=confirm_dialog(
            _("Approve this request"),
            _("Applies the change now and tells the requester."),
            ApproveRequestForm,
            submit=_("Approve"),
        ),
    )
    def approve_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Approve and apply the requested access change."""
        return self._decide(
            request,
            object_id,
            lambda item: access_requests.approve_request(
                item,
                request.user,
                approved_price=form.cleaned_data.get("approved_price"),
                note=str(form.cleaned_data.get("note", "")),
            ),
            str(_("Request approved and applied.")),
        )

    @action(
        description=_("Reject"),
        icon="block",
        permissions=["change"],
        url_path="reject-request",
        dialog=confirm_dialog(
            _("Reject this request"),
            _("The reason is sent to the requester."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Reject with a reason."""
        return self._decide(
            request,
            object_id,
            lambda item: access_requests.reject_request(
                item, request.user, note=str(form.cleaned_data["reason"])
            ),
            str(_("Request rejected.")),
        )


@register(AccessChangeLog)
class AccessChangeLogAdmin(ReadOnlyModelAdmin):
    """Append-only history of every access type or price change."""

    actions_row = ("revert_latest",)
    list_display = (
        "created_at",
        "firmware_label",
        "change",
        "by",
        "source",
        "reason",
    )
    list_filter = (
        ("source", ChoicesDropdownFilter),
        ("actor_kind", ChoicesDropdownFilter),
        ("to_tier", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("firmware_label", "reason", "actor__email")
    list_select_related = ("actor",)

    def has_revert_permission(self, request: HttpRequest) -> bool:
        """Staff who decide access change requests may also revert a change."""
        return bool(request.user.has_perm("firmwares.change_accesschangerequest"))

    @display(description=_("Change"))
    def change(self, obj: AccessChangeLog) -> str:
        """``Free → Featured``."""
        return (
            f"{tier_label(obj.from_tier, obj.from_price)} → "
            f"{tier_label(obj.to_tier, obj.to_price)}"
        )

    @display(description=_("By"))
    def by(self, obj: AccessChangeLog) -> str:
        """The person, or the system for automatic changes."""
        return str(obj.actor) if obj.actor else str(AccessActor(obj.actor_kind).label)

    @action(
        description=_("Revert"),
        icon="undo",
        permissions=["revert"],
        url_path="revert-change",
        dialog=confirm_dialog(
            _("Revert this change"),
            _(
                "Moves the file back to its earlier access type. Only the latest "
                "change of a file can be reverted; reverting an automatic "
                "promotion also stops it from being promoted again."
            ),
            ReasonForm,
            submit=_("Revert"),
        ),
    )
    def revert_latest(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Undo a change; the revert is itself recorded as a staff change."""
        log = self.get_object(request, unquote(object_id))
        if log is None:
            raise Http404
        try:
            access_changes.revert_change(
                log, actor=request.user, reason=str(form.cleaned_data["reason"])
            )
        except AccessChangeError as error:
            self.message_user(request, str(error), messages.ERROR)
        else:
            self.message_user(request, _("Change reverted."))
        return htmx_redirect(return_url(request, change_url(self, log.pk)))


@register(AutoFeatureSettings)
class AutoFeatureSettingsAdmin(SingletonModelAdmin):
    """Thresholds of the automatic 'popular free file → featured' promotion."""

    readonly_fields = ("last_run", "would_promote_now")
    fieldsets = (
        (
            _("Rule"),
            {
                "description": _(
                    "Free files that become extremely popular are moved to featured "
                    "automatically. Paid files are never touched and a file staff "
                    "took out of featured is never promoted again. Every promotion "
                    "is recorded in Access change history and can be reverted there."
                ),
                "fields": ("enabled", "dry_run", "notify_uploader"),
            },
        ),
        (
            _("Popularity thresholds"),
            {
                "fields": (
                    "window_days",
                    "min_downloads",
                    "min_unique_downloaders",
                    "velocity_days",
                    "min_velocity",
                ),
            },
        ),
        (
            _("Safety"),
            {"fields": ("cooldown_days", "max_promotions_per_run")},
        ),
        (
            _("Preview and last run"),
            {"fields": ("would_promote_now", "last_run")},
        ),
    )

    @display(description=_("Would be promoted right now"))
    def would_promote_now(self, obj: AutoFeatureSettings | None) -> SafeString | str:
        """Live evaluation of the thresholds (nothing is changed)."""
        if obj is None or not obj.pk:
            return "—"
        candidates, _skipped = auto_feature.find_candidates(obj)
        if not candidates:
            return str(_("No file meets every threshold."))
        return format_html(
            "<ul>{}</ul>",
            format_html_join(
                "",
                "<li>{} — {}</li>",
                (
                    (item.firmware.original_file_name, item.summary())
                    for item in candidates[:20]
                ),
            ),
        )

    @display(description=_("Last run"))
    def last_run(self, obj: AutoFeatureSettings | None) -> str:
        """When the rule last ran and what it did."""
        if obj is None or not obj.last_run_at:
            return str(_("Never run."))
        report: dict[str, Any] = obj.last_run_report or {}
        mode = "dry run" if report.get("dry_run") else "applied"
        count = len(report.get("promoted", [])) + len(report.get("would_promote", []))
        return f"{obj.last_run_at:%Y-%m-%d %H:%M} · {mode} · {count} file(s)"
