"""Staff review of end-user catalogue requests (Unfold admin).

Brand, model, variant, chipset and firmware requests are filed by signed-in users;
staff holding the model's ``change`` permission decide them here:

* **Approve** creates (or links) the real catalogue record from the requested data
  and tells the requester. In the confirm dialog the reviewer may first correct the
  name, slug and similar fields.
* **Reject** needs a reason, which is stored and sent to the requester.

Every decision is available as a row action, a detail-page action and a bulk
changelist action, and all of them run through ``apps.firmwares.catalog_requests``,
the same services the REST API uses, so locking, idempotency, audit history and
notifications cannot diverge. Pending requests stay editable until decided.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from django import forms
from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, IntegerField, QuerySet, Value, When
from django.db.models import Model as DjangoModel
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext as gettext_now
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextareaWidget, UnfoldAdminTextInputWidget

from apps.core.admin_base import (
    NoAddModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.core.admin_pages import render_admin_page
from apps.core.editor.admin import RichTextAdminMixin
from apps.core.editor.sanitizer import render_rich_text

from .catalog_requests import (
    Overrides,
    approve_brand_request,
    approve_chipset_request,
    approve_firmware_request,
    approve_model_request,
    approve_variant_request,
    decide_firmware_request,
    reject_catalog_request,
)
from .models import (
    BrandCreationRequest,
    ChipsetCreationRequest,
    ModelCreationRequest,
    VariantCreationRequest,
)
from .tracking_models import FirmwareRequest, FirmwareRequestSupport

logger = logging.getLogger(__name__)

BADGE_REQUEST_STATUS: Mapping[str, str] = {
    "pending": "warning",
    "open": "warning",
    "in_progress": "info",
    "approved": "success",
    "fulfilled": "success",
    "rejected": "danger",
}
BULK_TEMPLATE = "firmwares/admin/request_bulk_decision.html"
RICH_TEXT_NAMES = frozenset({"details", "description"})
Options = Mapping[str, str]
Decision = Callable[[Any], str]


def review_kwargs(options: Options) -> dict[str, Any]:
    """Keyword arguments of the approve services from a dialog's ``options``."""
    return {
        "reason": options.get("reason", ""),
        "overrides": Overrides.from_options(options),
    }


def _reason_field(label: str, *, required: bool, example: str) -> forms.CharField:
    return forms.CharField(
        label=label,
        required=required,
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3, "placeholder": example}),
    )


def _text_field(label: str, example: str, help_text: str, **kwargs: Any) -> Any:
    """An optional correction field: blank keeps the requested value."""
    kwargs.setdefault("required", False)
    return forms.CharField(
        label=label,
        help_text=help_text,
        widget=UnfoldAdminTextInputWidget(attrs={"placeholder": example}),
        **kwargs,
    )


class RejectRequestForm(BaseDialogForm):
    """Reject dialog: the reason is required, stored and sent to the requester."""

    reason = _reason_field(
        str(_("Reason")), required=True, example=str(_("Already in the catalogue"))
    )


class BulkDecisionForm(forms.Form):
    """The confirm page of a bulk approve/reject (not tied to one request)."""

    reason = forms.CharField(
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )

    def __init__(self, *args: Any, reject: bool, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["reason"]
        field.required = reject
        field.label = str(_("Reason") if reject else _("Note to the requesters"))
        field.help_text = str(
            _("Stored on every selected request and sent to its requester.")
            if reject
            else _("Optional; sent to every requester with the approval.")
        )
        field.widget.attrs["placeholder"] = str(
            _("Already in the catalogue") if reject else _("Thanks, it is live now")
        )


class ApprovalDialogForm(BaseDialogForm):
    """Approve dialog pre-filled from the request so the reviewer can correct it.

    Only values the reviewer filled in are passed on as overrides; blank means
    "use the requested data" (and, for the slug, "derive it from the name").
    """

    request_model: ClassVar[type[Any]]
    override_fields: ClassVar[tuple[str, ...]] = ()

    reason = _reason_field(
        str(_("Note to the requester (optional)")),
        required=False,
        example=str(_("Thanks, it is live now")),
    )

    def __init__(
        self,
        request: HttpRequest,
        object_id: int | str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(request, object_id, **kwargs)
        pending = (
            self.request_model.objects.filter(pk=unquote(str(object_id))).first()
            if object_id
            else None
        )
        if pending is not None:
            for name in self.override_fields:
                if name in self.fields:
                    self.fields[name].initial = getattr(pending, name, "")

    def options(self) -> dict[str, str]:
        """The reviewer's note and corrections, ready for the approve service."""
        values = {"reason": str(self.cleaned_data.get("reason", ""))}
        for name in self.override_fields:
            value = str(self.cleaned_data.get(name, "")).strip()
            if value:
                values[name] = value
        return values


def _slug_field() -> Any:
    return _text_field(
        str(_("Slug")),
        "redmi-note-13-pro-5g",
        str(_("The address part. Leave blank to build it from the name.")),
        required=False,
        max_length=160,
    )


class ApproveBrandForm(ApprovalDialogForm):
    """Approve a brand request, optionally correcting name and slug."""

    request_model = BrandCreationRequest
    override_fields = ("name", "slug")
    field_order = ("name", "slug", "reason")
    name = _text_field(
        str(_("Brand name")),
        "Xiaomi",
        str(_("As it will appear on the site.")),
        max_length=128,
    )
    slug = _slug_field()


class ApproveModelForm(ApprovalDialogForm):
    """Approve a model request, optionally correcting name, slug and code."""

    request_model = ModelCreationRequest
    override_fields = ("name", "slug", "model_code")
    field_order = ("name", "slug", "model_code", "reason")
    name = _text_field(
        str(_("Model name")),
        "Redmi Note 13 Pro 5G",
        str(_("The marketing name of the device.")),
        max_length=128,
    )
    slug = _slug_field()
    model_code = _text_field(
        str(_("Model code")),
        "2312DRA50G",
        str(_("The manufacturer's model number.")),
        required=False,
        max_length=64,
    )


class ApproveVariantForm(ApprovalDialogForm):
    """Approve a variant request, optionally correcting its identifying fields."""

    request_model = VariantCreationRequest
    override_fields = ("name", "slug", "model_code", "region")
    field_order = ("name", "slug", "model_code", "region", "reason")
    name = _text_field(
        str(_("Variant name")),
        "AI2401_A",
        str(_("Shown in the variant picker.")),
        max_length=128,
    )
    slug = _slug_field()
    model_code = _text_field(
        str(_("Variant code")),
        "AI2401_A",
        str(_("Saved upper-case; it must be unique within the model.")),
        required=False,
        max_length=64,
    )
    region = _text_field(
        str(_("Region or market")),
        "Global",
        str(_("For example Global, EU, India.")),
        required=False,
        max_length=64,
    )


class ApproveChipsetForm(ApprovalDialogForm):
    """Approve a chipset request, optionally correcting vendor, name and slug."""

    request_model = ChipsetCreationRequest
    override_fields = ("vendor", "name", "slug")
    field_order = ("vendor", "name", "slug", "reason")
    vendor = _text_field(
        str(_("Vendor")), "MediaTek", str(_("The chipset maker.")), max_length=64
    )
    name = _text_field(
        str(_("Chipset name")),
        "Dimensity 9200 (MT6985)",
        str(_("Without the vendor name.")),
        max_length=128,
    )
    slug = _slug_field()


class ApproveFirmwareRequestForm(ApprovalDialogForm):
    """Approve a firmware request, optionally correcting the variant to create."""

    request_model = FirmwareRequest
    override_fields = ("variant_name",)
    field_order = ("variant_name", "reason")
    variant_name = _text_field(
        str(_("Variant")),
        "AI2401_A",
        str(_("Created under the model when it does not exist yet.")),
        required=False,
        max_length=200,
    )


def approval_dialog(form_class: type[BaseDialogForm]) -> Any:
    """Dialog options of an Approve action using ``form_class``."""
    return confirm_dialog(
        _("Approve this request"),
        _(
            "Creates or links the catalogue record from the requested data and "
            "notifies the requester. You may correct the data first."
        ),
        form_class,
        submit=_("Approve"),
    )


def _problem_text(error: PermissionDenied | ValidationError) -> str:
    return " ".join(getattr(error, "messages", [str(error)]))


class RequestReviewAdmin(NoAddModelAdmin):
    """Shared review behaviour of the five request queues.

    Subclasses name the status that means "waiting", the fields a reviewer may
    still edit while it waits, and how one request is approved.
    """

    pending_status: ClassVar[str] = "pending"
    data_fields: ClassVar[tuple[str, ...]] = ()
    actions: Sequence[str] = ("approve_selected", "reject_selected")
    actions_row: Sequence[str] = ("approve_request", "reject_request")
    actions_detail: Sequence[str] = ("approve_request", "reject_request")
    list_filter: Sequence[Any] = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    list_select_related: Sequence[str] = ("requested_by", "reviewed_by")

    @display(description=_("Status"), ordering="status", label=BADGE_REQUEST_STATUS)
    def status_badge(self, obj: Any) -> str:
        """The request status as a coloured badge."""
        return str(obj.status)

    def get_readonly_fields(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> Sequence[str]:
        """Pending requests keep their data editable; decided ones are frozen.

        Rich-text fields that cannot be edited are shown rendered, not as markup.
        """
        names = [field.name for field in self.model._meta.fields]
        editable: tuple[str, ...] = ()
        if obj is not None and getattr(obj, "status", "") == self.pending_status:
            editable = self.data_fields
        return [
            f"{name}_rendered" if name in RICH_TEXT_NAMES else name
            for name in names
            if name not in editable
        ]

    @display(description=_("Details"))
    def details_rendered(self, obj: Any) -> str:
        """The requester's formatted details, re-sanitised for display."""
        return render_rich_text(obj.details) or "—"

    @display(description=_("Details"))
    def description_rendered(self, obj: Any) -> str:
        """The requester's formatted description, re-sanitised for display."""
        return render_rich_text(obj.description) or "—"

    def _waiting_first(self) -> Any:
        return Case(
            When(status=self.pending_status, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ).asc()

    def get_ordering(self, request: HttpRequest) -> Sequence[Any]:
        """Waiting requests first, then newest first."""
        return (self._waiting_first(), "-created_at")

    # -- hooks ---------------------------------------------------------------

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Approve ``pending`` through its service; return the success message."""
        raise NotImplementedError

    def reject_one(self, request: HttpRequest, pending: Any, reason: str) -> str:
        """Reject ``pending`` through its service; return the success message."""
        reject_catalog_request(pending, request.user, reason=reason)
        return str(_("Request rejected."))

    # -- single request (row and detail actions) -----------------------------

    def _decide_one(
        self, request: HttpRequest, object_id: str, decide: Decision
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        try:
            outcome = decide(target)
        except (PermissionDenied, ValidationError) as error:
            self.message_user(request, _problem_text(error), messages.ERROR)
        else:
            self.message_user(request, outcome)
        return htmx_redirect(return_url(request, change_url(self, target.pk)))

    def approve_dialog(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Approve one request with the corrections typed in the dialog."""
        options = form.options() if isinstance(form, ApprovalDialogForm) else {}
        return self._decide_one(
            request,
            object_id,
            lambda target: self.approve_one(request, target, options),
        )

    @action(
        description=_("Reject request"),
        icon="block",
        permissions=["change"],
        url_path="reject-request",
        dialog=confirm_dialog(
            _("Reject this request"),
            _("The reason is stored on the request and sent to the requester."),
            RejectRequestForm,
            submit=_("Reject"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Row/detail action: reject one request with a reason."""
        reason = str(form.cleaned_data["reason"])
        return self._decide_one(
            request,
            object_id,
            lambda target: self.reject_one(request, target, reason),
        )

    # -- bulk (changelist) actions --------------------------------------------

    @action(description=_("Approve selected requests…"), permissions=["change"])
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[Any]
    ) -> HttpResponse | None:
        """Bulk approve after a confirmation page with an optional note."""
        return self._bulk(request, queryset, reject=False)

    @action(description=_("Reject selected requests…"), permissions=["change"])
    def reject_selected(
        self, request: HttpRequest, queryset: QuerySet[Any]
    ) -> HttpResponse | None:
        """Bulk reject after a confirmation page that asks for the reason."""
        return self._bulk(request, queryset, reject=True)

    def _bulk(
        self, request: HttpRequest, queryset: QuerySet[Any], *, reject: bool
    ) -> HttpResponse | None:
        waiting = list(queryset.filter(status=self.pending_status))
        if not waiting:
            self.message_user(
                request,
                _("None of the selected requests is waiting."),
                messages.WARNING,
            )
            return None
        confirmed = "confirm" in request.POST
        form = BulkDecisionForm(request.POST if confirmed else None, reject=reject)
        if confirmed and form.is_valid():
            self._apply_bulk(request, waiting, str(form.cleaned_data["reason"]), reject)
            return None
        verb = _("Reject") if reject else _("Approve")
        return render_admin_page(
            request,
            BULK_TEMPLATE,
            title=f"{verb} {len(waiting)} {self.opts.verbose_name_plural}",
            context={
                "form": form,
                "requests": waiting,
                "reject": reject,
                "action_name": "reject_selected" if reject else "approve_selected",
                "cancel_url": request.get_full_path(),
                "opts": self.opts,
            },
        )

    def _apply_bulk(
        self, request: HttpRequest, waiting: list[Any], reason: str, reject: bool
    ) -> None:
        done, problems = 0, []
        for pending in waiting:
            try:
                if reject:
                    self.reject_one(request, pending, reason)
                else:
                    self.approve_one(request, pending, {"reason": reason})
            except (PermissionDenied, ValidationError) as error:
                problems.append(f"{pending}: {_problem_text(error)}")
            else:
                done += 1
        verb = gettext_now("rejected") if reject else gettext_now("approved")
        self.message_user(
            request,
            gettext_now("%(count)d request(s) %(verb)s.")
            % {"count": done, "verb": verb},
        )
        for problem in problems:
            self.message_user(request, problem, messages.ERROR)


def _created_message(created: bool, kind: str, name: str) -> str:
    if created:
        return gettext_now("Approved: created the %(kind)s “%(name)s”.") % {
            "kind": kind,
            "name": name,
        }
    return gettext_now("Approved: linked to the existing %(kind)s “%(name)s”.") % {
        "kind": kind,
        "name": name,
    }


def _approve_action(form_class: type[BaseDialogForm]) -> Callable[..., Any]:
    """The Approve row/detail action of a queue using ``form_class``."""

    @action(
        description=_("Approve and create"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-request",
        dialog=approval_dialog(form_class),
    )
    def approve_request(
        self: RequestReviewAdmin,
        request: HttpRequest,
        form: BaseDialogForm,
        object_id: str,
    ) -> HttpResponse:
        return self.approve_dialog(request, form, object_id)

    return approve_request


class BrandCreationRequestAdmin(RequestReviewAdmin):
    """Review queue of brand requests."""

    data_fields = ("name",)
    list_display = ("name", "status_badge", "requested_by", "reviewed_by", "created_at")
    search_fields = ("name", "requested_by__email")
    approve_request = _approve_action(ApproveBrandForm)

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Create or link the brand."""
        brand, created = approve_brand_request(
            pending, request.user, **review_kwargs(options)
        )
        return _created_message(created, gettext_now("brand"), brand.name)


class ModelCreationRequestAdmin(RequestReviewAdmin):
    """Review queue of model requests."""

    data_fields = ("name", "model_code", "brand", "chipset")
    autocomplete_fields = ("brand", "chipset")
    list_display = ("name", "brand", "status_badge", "requested_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "model_code", "brand__name", "requested_by__email")
    approve_request = _approve_action(ApproveModelForm)

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Create or link the model under the requested brand."""
        model, created = approve_model_request(
            pending, request.user, **review_kwargs(options)
        )
        return _created_message(created, gettext_now("model"), model.name)


class VariantCreationRequestAdmin(RequestReviewAdmin):
    """Review queue of variant requests."""

    data_fields = ("name", "model_code", "region", "memory", "carrier", "model")
    autocomplete_fields = ("model",)
    list_display = (
        "name",
        "model",
        "model_code",
        "status_badge",
        "requested_by",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("model__brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "name",
        "model_code",
        "model__name",
        "model__brand__name",
        "requested_by__email",
    )
    approve_request = _approve_action(ApproveVariantForm)

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Create or link the variant under the requested model."""
        variant, created = approve_variant_request(
            pending, request.user, **review_kwargs(options)
        )
        return _created_message(created, gettext_now("variant"), variant.name)


class ChipsetCreationRequestAdmin(RequestReviewAdmin):
    """Review queue of chipset requests."""

    data_fields = ("vendor", "name", "family")
    list_display = (
        "name",
        "vendor",
        "family",
        "status_badge",
        "requested_by",
        "created_at",
    )
    search_fields = ("name", "vendor", "family", "requested_by__email")
    approve_request = _approve_action(ApproveChipsetForm)

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Create or link the chipset."""
        chipset, created = approve_chipset_request(
            pending, request.user, **review_kwargs(options)
        )
        return _created_message(created, gettext_now("chipset"), chipset.display_name)


class FirmwareRequestSupportInline(TabularInline):
    """The users who asked for the same firmware (the request's demand)."""

    model = FirmwareRequestSupport
    fields = ("user", "created_at")
    readonly_fields = ("user", "created_at")
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        """Support rows are created by users asking, never by staff."""
        return False


class FulfilRequestForm(BaseDialogForm):
    """Optional note sent with a fulfilled firmware request."""

    reason = _reason_field(
        str(_("Note to the requesters (optional)")),
        required=False,
        example=str(_("Published under Redmi Note 13 Pro 5G")),
    )


class FirmwareRequestAdmin(RichTextAdminMixin, RequestReviewAdmin):
    """Review queue of firmware requests (status ``open``)."""

    rich_text_fields: ClassVar[Mapping[str, str]] = {"description": "request"}
    pending_status = "open"
    data_fields = ("variant_name", "firmware_type", "urgency", "description")
    actions_row = ("approve_request", "fulfil_request", "reject_request")
    actions_detail = ("approve_request", "fulfil_request", "reject_request")
    inlines = (FirmwareRequestSupportInline,)
    list_display = (
        "brand",
        "model",
        "variant_name",
        "firmware_type",
        "status_badge",
        "request_count",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("firmware_type", ChoicesDropdownFilter),
        ("urgency", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("brand__name", "model__name", "variant_name")
    list_select_related = ("brand", "model", "reviewed_by")
    approve_request = _approve_action(ApproveFirmwareRequestForm)

    def get_ordering(self, request: HttpRequest) -> Sequence[Any]:
        """Open requests first, then the most requested."""
        return (self._waiting_first(), "-request_count", "-last_requested_at")

    def approve_one(self, request: HttpRequest, pending: Any, options: Options) -> str:
        """Accept the request and make sure the requested variant exists."""
        variant, created = approve_firmware_request(
            pending, request.user, **review_kwargs(options)
        )
        if variant is None:
            return gettext_now("Approved: the request is now in progress.")
        return _created_message(created, gettext_now("variant"), variant.name)

    def reject_one(self, request: HttpRequest, pending: Any, reason: str) -> str:
        """Reject the request for every supporter."""
        decide_firmware_request(pending, request.user, status="rejected", reason=reason)
        return str(_("Request rejected."))

    @action(
        description=_("Mark fulfilled"),
        icon="task_alt",
        permissions=["change"],
        url_path="fulfil-request",
        dialog=confirm_dialog(
            _("The firmware has been added"),
            _("Everyone who asked for it is notified."),
            FulfilRequestForm,
            submit=_("Mark fulfilled"),
        ),
    )
    def fulfil_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        """Row/detail action: the firmware is published; tell the supporters."""
        reason = str(form.cleaned_data["reason"])

        def decide(target: Any) -> str:
            decide_firmware_request(
                target, request.user, status="fulfilled", reason=reason
            )
            return str(_("Request marked fulfilled."))

        return self._decide_one(request, object_id, decide)


REQUEST_ADMINS: tuple[tuple[type[DjangoModel], type[RequestReviewAdmin]], ...] = (
    (BrandCreationRequest, BrandCreationRequestAdmin),
    (ModelCreationRequest, ModelCreationRequestAdmin),
    (VariantCreationRequest, VariantCreationRequestAdmin),
    (ChipsetCreationRequest, ChipsetCreationRequestAdmin),
    (FirmwareRequest, FirmwareRequestAdmin),
)

for _model_class, _admin_class in REQUEST_ADMINS:
    register(_model_class)(_admin_class)
