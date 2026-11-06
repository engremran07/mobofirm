"""Firmware catalogue, moderation and download administration (Unfold).

Firmware records are only created and published through the moderation services in
``apps.firmwares.services``: the admin never adds typed firmware by hand and its
review fields are read-only, so approval/rejection/withdrawal always runs through
the same validated, audited code path as every other entry point.
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
from django.db import IntegrityError, transaction
from django.db.models import Count, QuerySet
from django.db.models import Model as DjangoModel
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import ActionForm, BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget

from apps.core.admin_base import (
    BADGE_APPROVAL,
    BADGE_JOB,
    ModelAdmin,
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

from .admin_autofill import FirmwareAutoFill
from .constants import MAIN_CATEGORIES
from .models import (
    AdGateLog,
    AutomatedBlogLink,
    Brand,
    BrandCreationRequest,
    BrandSchema,
    ChangelogEntry,
    Chipset,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    FirmwareDiff,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    GSMArenaDevice,
    HotlinkBlock,
    Model,
    ModelCreationRequest,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    PendingFirmware,
    ReadbackFirmware,
    ReleaseNote,
    SchemaUpdateProposal,
    TestResult,
    TrustedTester,
    UnclassifiedFirmware,
    Variant,
    VariantCreationRequest,
    VerificationCredit,
    VerificationReport,
)
from .services import (
    approve_pending_firmware,
    reject_pending_firmware,
    withdraw_firmware,
)
from .tracking_models import (
    FirmwareDownloadAttempt,
    FirmwareRequest,
    FirmwareStats,
    FirmwareView,
)

logger = logging.getLogger(__name__)

# Status values of the firmware workflows -> Unfold badge colour.
BADGE_STATE: Mapping[str, str] = {
    **BADGE_APPROVAL,
    **BADGE_JOB,
    "active": "success",
    "used": "info",
    "expired": "warning",
    "revoked": "danger",
    "started": "info",
    "aborted": "warning",
    "submitted": "info",
    "published": "success",
    "pass": "success",
    "partial": "warning",
    "fail": "danger",
    "skip": "info",
    "open": "warning",
    "fulfilled": "success",
    "verified": "success",
    "unreviewed": "warning",
    "hidden": "danger",
    "complete": "success",
    "incomplete": "warning",
    "safe": "success",
    "moderate": "info",
    "advanced": "warning",
    "risky": "danger",
}

ReviewHandler = Callable[..., object]


def external_link(url: str) -> SafeString | str:
    """Read-only link to an external catalogue/download URL (no inline style)."""
    if not url:
        return "—"
    if not url.startswith(("https://", "http://")):
        return url
    return format_html(
        '<a class="text-primary-600 underline dark:text-primary-500" href="{}" '
        'target="_blank" rel="noopener noreferrer nofollow">{}</a>',
        url,
        url,
    )


# ---------------------------------------------------------------------------
# Catalogue: chipsets, brands, models, variants
# ---------------------------------------------------------------------------


@register(Chipset)
class ChipsetAdmin(ModelAdmin):
    list_display = ("display_name", "vendor", "family", "slug", "model_count")
    list_filter = (
        ("vendor", ChoicesDropdownFilter),
        ("family", ChoicesDropdownFilter),
    )
    search_fields = ("name", "vendor", "family", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {
        "slug": ("vendor", "name")
    }
    readonly_fields = ("created_at", "updated_at")

    @display(description=_("Models"), ordering="models_total")
    def model_count(self, obj: Chipset) -> int:
        return int(getattr(obj, "models_total", 0))

    def get_queryset(self, request: HttpRequest) -> QuerySet[Chipset]:
        return super().get_queryset(request).annotate(models_total=Count("models"))


@register(AutomatedBlogLink)
class AutomatedBlogLinkAdmin(NoAddModelAdmin):
    list_display = ("kind", "brand", "model", "chipset", "post", "last_synced_at")
    list_filter = (
        ("kind", ChoicesDropdownFilter),
        ("last_synced_at", RangeDateTimeFilter),
    )
    list_select_related = ("brand", "model", "chipset", "post")
    raw_id_fields = ("brand", "model", "chipset", "post")
    readonly_fields = ("content_hash", "last_synced_at", "created_at", "updated_at")


class BrandModelInline(TabularInline):
    model = Model
    fields = ("name", "slug", "model_code", "status", "is_active")
    readonly_fields = ("name", "slug", "model_code", "status", "is_active")
    show_change_link = True
    can_delete = False
    tab = True
    ordering = ("name",)
    verbose_name_plural = _("Models")

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(Brand)
class BrandAdmin(ModelAdmin):
    list_display = (
        "name",
        "slug",
        "model_count",
        "is_featured",
        "autofill_status",
        "created_at",
    )
    list_filter = ("is_featured", ("created_at", RangeDateTimeFilter))
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "website_link", "logo_link")
    ordering = ("name",)
    inlines = (BrandModelInline,)
    actions_detail = ("autofill_description",)
    fieldsets = (
        (
            _("Basic information"),
            {"fields": ("name", "slug", "is_featured"), "classes": ["tab"]},
        ),
        (
            _("Catalogue details"),
            {
                "fields": (
                    "description",
                    "website_url",
                    "website_link",
                    "logo_url",
                    "logo_link",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Models"), ordering="models_total")
    def model_count(self, obj: Brand) -> int:
        return int(getattr(obj, "models_total", 0))

    @display(
        description=_("Catalogue status"),
        label={"complete": "success", "incomplete": "warning"},
    )
    def autofill_status(self, obj: Brand) -> str:
        return "complete" if obj.description else "incomplete"

    @display(description=_("Website"))
    def website_link(self, obj: Brand) -> SafeString | str:
        return external_link(obj.website_url)

    @display(description=_("Logo URL"))
    def logo_link(self, obj: Brand) -> SafeString | str:
        return external_link(obj.logo_url)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Brand]:
        return super().get_queryset(request).annotate(models_total=Count("models"))

    @action(
        description=_("Auto-fill description"),
        icon="auto_fix_high",
        permissions=["change"],
        url_path="autofill-description",
        dialog=confirm_dialog(
            _("Auto-fill missing brand details"),
            _(
                "Looks the brand up in the synchronised GSMArena records and fills "
                "an empty description. Existing text is never overwritten."
            ),
            submit=_("Auto-fill"),
        ),
    )
    def autofill_description(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        brand = self.get_object(request, unquote(object_id))
        if brand is None:
            raise Http404
        if brand.description:
            self.message_user(
                request, _("The brand already has a description."), messages.WARNING
            )
        else:
            description = FirmwareAutoFill.autofill_brand(brand.name).get(
                "description", ""
            )
            if description:
                brand.description = description
                brand.save(update_fields=["description", "updated_at"])
                self.message_user(request, _("Brand description filled in."))
            else:
                self.message_user(
                    request,
                    _("No description was found for this brand."),
                    messages.WARNING,
                )
        return htmx_redirect(return_url(request, change_url(self, brand.pk)))


class VariantInline(TabularInline):
    model = Variant
    fields = ("name", "slug", "model_code", "region", "chipset", "is_active")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    show_change_link = True
    tab = True
    ordering = ("name",)
    verbose_name_plural = _("Variants")


@register(Model)
class DeviceModelAdmin(ModelAdmin):
    list_display = (
        "name",
        "brand",
        "model_code",
        "chipset_ref",
        "status",
        "variant_count",
        "created_at",
    )
    list_filter = (
        ("brand", RelatedDropdownFilter),
        ("status", ChoicesDropdownFilter),
        "is_active",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "slug", "brand__name", "model_code", "codename")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "image_link")
    autocomplete_fields = ("brand", "chipset_ref")
    ordering = ("brand__name", "name")
    inlines = (VariantInline,)
    fieldsets = (
        (
            _("Identity"),
            {
                "fields": (
                    "brand",
                    "name",
                    "slug",
                    "marketing_name",
                    "codename",
                    "model_code",
                    "model_codes",
                    "also_known_as",
                    "description",
                    "status",
                    "is_active",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Hardware"),
            {
                "fields": (
                    "chipset",
                    "chipset_ref",
                    "cpu",
                    "gpu",
                    "network_technology",
                    "os_version",
                    "ram_options",
                    "storage_options",
                    "colors",
                    "display_size",
                    "battery",
                    "dimensions",
                    "weight",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Release"),
            {
                "fields": ("release_date", "announced_date", "regions"),
                "classes": ["tab"],
            },
        ),
        (
            _("Media"),
            {"fields": ("image_url", "image_link"), "classes": ["tab"]},
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Variants"), ordering="variants_total")
    def variant_count(self, obj: Model) -> int:
        return int(getattr(obj, "variants_total", 0))

    @display(description=_("Device photo URL"))
    def image_link(self, obj: Model) -> SafeString | str:
        return external_link(obj.image_url)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Model]:
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "chipset_ref")
            .annotate(variants_total=Count("variants"))
        )


@register(Variant)
class VariantAdmin(ModelAdmin):
    list_display = (
        "name",
        "model",
        "brand_name",
        "model_code",
        "region",
        "chipset_ref",
        "is_active",
        "created_at",
    )
    list_filter = (
        ("model__brand", RelatedDropdownFilter),
        "is_active",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "name",
        "slug",
        "model__name",
        "model__brand__name",
        "model_code",
        "region",
        "board_id",
    )
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("model", "chipset_ref")
    ordering = ("model__brand__name", "model__name", "name")
    fieldsets = (
        (
            _("Identification"),
            {
                "fields": (
                    "model",
                    "name",
                    "slug",
                    "model_code",
                    "region",
                    "board_id",
                    "is_active",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Hardware"),
            {
                "fields": ("chipset", "chipset_ref", "ram_options", "storage_options"),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Brand"), ordering="model__brand__name")
    def brand_name(self, obj: Variant) -> str:
        return obj.model.brand.name

    def get_queryset(self, request: HttpRequest) -> QuerySet[Variant]:
        return (
            super().get_queryset(request).select_related("model__brand", "chipset_ref")
        )


# ---------------------------------------------------------------------------
# Brand schemas and catalogue creation requests
# ---------------------------------------------------------------------------


@register(BrandSchema)
class BrandSchemaAdmin(ModelAdmin):
    list_display = ("brand", "approved_by", "approved_at", "created_at")
    list_filter = (("approved_at", RangeDateTimeFilter),)
    search_fields = ("brand__name",)
    autocomplete_fields = ("brand",)
    raw_id_fields = ("approved_by",)


@register(SchemaUpdateProposal)
class SchemaUpdateProposalAdmin(ModelAdmin):
    list_display = ("brand", "status_badge", "proposed_by", "reviewed_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("brand__name", "proposed_by__email")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("brand",)
    raw_id_fields = ("proposed_by", "reviewed_by")

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: SchemaUpdateProposal) -> str:
        return obj.status


class CreationRequestForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


class CreationRequestAdmin(ModelAdmin):
    """Catalogue requests: staff reject with a recorded reason."""

    list_filter: Sequence[tuple[str, Any]] = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    readonly_fields = ("status", "reviewed_by", "reviewed_at", "created_at")
    list_select_related = ("requested_by", "reviewed_by")
    raw_id_fields = ("requested_by", "reviewed_by")
    actions: Sequence[str] = ("reject_requests",)
    actions_row: Sequence[str] = ("reject_request",)
    actions_detail: Sequence[str] = ("reject_request",)

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: Any) -> str:
        return str(obj.status)

    def _reject(
        self, request: HttpRequest, queryset: QuerySet[Any], reason: str
    ) -> int:
        return queryset.filter(status="pending").update(
            status="rejected",
            reviewed_by=request.user,
            reviewed_at=timezone.now(),
            notes=reason,
        )

    @action(description=_("Reject selected requests"), permissions=["change"])
    def reject_requests(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        count = self._reject(request, queryset, "")
        self.message_user(
            request, _("%(count)d request(s) rejected.") % {"count": count}
        )

    @action(
        description=_("Reject request"),
        icon="block",
        permissions=["change"],
        url_path="reject-request",
        dialog=confirm_dialog(
            _("Reject this request"),
            _("The reason is stored on the request."),
            CreationRequestForm,
            submit=_("Reject"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        changed = self._reject(
            request,
            self.model.objects.filter(pk=target.pk),
            str(form.cleaned_data["reason"]),
        )
        if changed:
            self.message_user(request, _("Request rejected."))
        else:
            self.message_user(
                request, _("Only pending requests can be rejected."), messages.ERROR
            )
        return htmx_redirect(return_url(request, change_url(self, target.pk)))


@register(BrandCreationRequest)
class BrandCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "status_badge", "requested_by", "reviewed_by", "created_at")
    search_fields = ("name", "requested_by__email")
    actions = ("approve_requests", "reject_requests")
    actions_row = ("approve_request", "reject_request")
    actions_detail = ("approve_request", "reject_request")

    def _approve(self, request: HttpRequest, pending: BrandCreationRequest) -> bool:
        """Create the brand and close the request in one transaction."""
        slug = slugify(pending.name)
        try:
            with transaction.atomic():
                brand, _created = Brand.objects.get_or_create(
                    slug=slug, defaults={"name": pending.name}
                )
                pending.status = "approved"
                pending.reviewed_by_id = request.user.pk
                pending.reviewed_at = timezone.now()
                pending.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        except IntegrityError:
            self.message_user(
                request,
                _("Brand “%(name)s” conflicts with an existing brand or reserved URL.")
                % {"name": pending.name},
                messages.ERROR,
            )
            return False
        logger.info("Brand request %s approved as brand %s", pending.pk, brand.pk)
        return True

    @action(description=_("Approve selected requests"), permissions=["change"])
    def approve_requests(
        self, request: HttpRequest, queryset: QuerySet[BrandCreationRequest]
    ) -> None:
        count = sum(
            self._approve(request, pending)
            for pending in queryset.filter(status="pending")
        )
        self.message_user(
            request, _("%(count)d brand(s) approved and created.") % {"count": count}
        )

    @action(
        description=_("Approve and create brand"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-request",
        dialog=confirm_dialog(
            _("Approve this brand request"),
            _("Creates the brand (or reuses an existing one with the same slug)."),
            submit=_("Approve"),
        ),
    )
    def approve_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        if target.status != "pending":
            self.message_user(
                request, _("Only pending requests can be approved."), messages.ERROR
            )
        elif self._approve(request, target):
            self.message_user(request, _("Brand created."))
        return htmx_redirect(return_url(request, change_url(self, target.pk)))


@register(ModelCreationRequest)
class ModelCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "brand", "status_badge", "requested_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "brand__name", "requested_by__email")
    autocomplete_fields = ("brand",)


@register(VariantCreationRequest)
class VariantCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "model", "status_badge", "requested_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("model__brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "model__name", "model__brand__name", "requested_by__email")
    autocomplete_fields = ("model",)


# ---------------------------------------------------------------------------
# Firmware moderation
# ---------------------------------------------------------------------------


class FirmwareReviewActionForm(ActionForm):
    """Extra inputs for the bulk review actions on the changelist."""

    category = forms.ChoiceField(
        choices=[("", "— category (required to approve) —"), *MAIN_CATEGORIES],
        required=False,
        label="Firmware category",
        widget=UnfoldAdminSelectWidget,
    )
    notes = forms.CharField(
        required=False,
        max_length=1000,
        label="Decision notes / rejection reason",
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 2}),
    )


class ApproveUploadForm(BaseDialogForm):
    category = forms.ChoiceField(
        choices=MAIN_CATEGORIES,
        label=_("Firmware category"),
        widget=UnfoldAdminSelectWidget,
    )
    notes = forms.CharField(
        required=False,
        max_length=1000,
        label=_("Decision notes"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


@register(PendingFirmware)
class PendingFirmwareAdmin(NoAddModelAdmin):
    action_form = FirmwareReviewActionForm
    actions = ("approve_selected", "reject_selected", "withdraw_selected")
    actions_row = ("approve_upload", "reject_upload", "withdraw_upload")
    actions_detail = ("approve_upload", "reject_upload", "withdraw_upload")
    list_display = (
        "original_file_name",
        "source_type",
        "uploader",
        "decision_badge",
        "ai_brand",
        "ai_model",
        "ai_category",
        "package_format",
        "reviewed_by",
        "created_at",
    )
    list_filter = (
        ("admin_decision", ChoicesDropdownFilter),
        ("source_type", ChoicesDropdownFilter),
        ("extraction_status", ChoicesDropdownFilter),
        ("ai_category", ChoicesDropdownFilter),
        ("package_format", ChoicesDropdownFilter),
        "is_password_protected",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "original_file_name",
        "ai_brand",
        "ai_model",
        "ai_variant",
        "chipset",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "id",
        "source_type",
        "stored_file_path",
        "external_url",
        "external_link",
        "file_size",
        "file_hash",
        "admin_decision",
        "admin_notes",
        "category_locked",
        "reviewed_by",
        "reviewed_at",
        "routed_category",
        "routed_object_id",
    )
    autocomplete_fields = ("uploaded_brand", "uploaded_model", "uploaded_variant")
    list_select_related = ("uploader", "reviewed_by")
    raw_id_fields = ("uploader",)
    fieldsets = (
        (
            _("File"),
            {
                "fields": (
                    "id",
                    "original_file_name",
                    "source_type",
                    "stored_file_path",
                    "external_url",
                    "external_link",
                    "file_size",
                    "file_hash",
                    "uploader",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Classification"),
            {
                "fields": (
                    "uploaded_brand",
                    "uploaded_model",
                    "uploaded_variant",
                    "package_format",
                    "ai_brand",
                    "ai_model",
                    "ai_variant",
                    "ai_category",
                    "ai_subtype",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Review"),
            {
                "fields": (
                    "admin_decision",
                    "admin_notes",
                    "category_locked",
                    "reviewed_by",
                    "reviewed_at",
                    "routed_category",
                    "routed_object_id",
                    "extraction_status",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Technical"),
            {
                "fields": (
                    "chipset",
                    "partitions",
                    "metadata",
                    "is_password_protected",
                    "encrypted_password",
                    "password_validation_status",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Timestamps"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Decision"), ordering="admin_decision", label=BADGE_STATE)
    def decision_badge(self, obj: PendingFirmware) -> str:
        return obj.admin_decision

    @display(description=_("External download"))
    def external_link(self, obj: PendingFirmware) -> SafeString | str:
        return external_link(obj.external_url)

    def _review(
        self,
        request: HttpRequest,
        queryset: QuerySet[PendingFirmware],
        handler: ReviewHandler,
        done_label: str,
        **kwargs: str,
    ) -> None:
        done = 0
        problems: list[str] = []
        for pending in queryset:
            try:
                handler(pending, request.user, **kwargs)
            except (PermissionDenied, ValidationError) as exc:
                detail = " ".join(getattr(exc, "messages", [str(exc)]))
                problems.append(f"{pending.original_file_name}: {detail}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done_label} {done} upload(s).")
        for problem in problems:
            self.message_user(request, problem, level=messages.ERROR)

    def _review_one(
        self,
        request: HttpRequest,
        object_id: str,
        handler: ReviewHandler,
        done_label: str,
        **kwargs: str,
    ) -> HttpResponse:
        pending = self.get_object(request, unquote(object_id))
        if pending is None:
            raise Http404
        self._review(
            request,
            PendingFirmware.objects.filter(pk=pending.pk),
            handler,
            done_label,
            **kwargs,
        )
        return htmx_redirect(return_url(request, change_url(self, pending.pk)))

    @action(
        description="Approve selected uploads (choose a category)",
        permissions=["change"],
    )
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[PendingFirmware]
    ) -> None:
        category = request.POST.get("category", "")
        if not category:
            self.message_user(
                request,
                "Choose a firmware category before approving.",
                level=messages.ERROR,
            )
            return
        self._review(
            request,
            queryset,
            approve_pending_firmware,
            "Approved",
            category=category,
            notes=request.POST.get("notes", ""),
        )

    @action(
        description="Reject selected uploads (reason required)", permissions=["change"]
    )
    def reject_selected(
        self, request: HttpRequest, queryset: QuerySet[PendingFirmware]
    ) -> None:
        self._review(
            request,
            queryset,
            reject_pending_firmware,
            "Rejected",
            notes=request.POST.get("notes", ""),
        )

    @action(
        description="Withdraw approved uploads back to pending (reason required)",
        permissions=["change"],
    )
    def withdraw_selected(
        self, request: HttpRequest, queryset: QuerySet[PendingFirmware]
    ) -> None:
        self._review(
            request,
            queryset,
            withdraw_firmware,
            "Withdrew",
            reason=request.POST.get("notes", ""),
        )

    @action(
        description=_("Approve"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-upload",
        dialog=confirm_dialog(
            _("Approve this upload"),
            _("Routes the file into the chosen category and publishes it."),
            ApproveUploadForm,
            submit=_("Approve"),
        ),
    )
    def approve_upload(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            approve_pending_firmware,
            "Approved",
            category=str(form.cleaned_data["category"]),
            notes=str(form.cleaned_data["notes"]),
        )

    @action(
        description=_("Reject"),
        icon="block",
        permissions=["change"],
        url_path="reject-upload",
        dialog=confirm_dialog(
            _("Reject this upload"),
            _("The reason is stored and sent to the uploader."),
            ReasonForm,
            submit=_("Reject"),
        ),
    )
    def reject_upload(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            reject_pending_firmware,
            "Rejected",
            notes=str(form.cleaned_data["reason"]),
        )

    @action(
        description=_("Withdraw"),
        icon="undo",
        permissions=["change"],
        url_path="withdraw-upload",
        dialog=confirm_dialog(
            _("Withdraw this approved upload"),
            _("Unpublishes the firmware and returns the upload to pending review."),
            ReasonForm,
            submit=_("Withdraw"),
        ),
    )
    def withdraw_upload(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._review_one(
            request,
            object_id,
            withdraw_firmware,
            "Withdrew",
            reason=str(form.cleaned_data["reason"]),
        )


class BaseFirmwareAdmin(NoAddModelAdmin):
    """Typed firmware records are created only by approving a pending upload."""

    actions = ("activate_selected", "deactivate_selected")
    list_display = (
        "original_file_name",
        "brand",
        "model",
        "variant",
        "chipset",
        "source_type",
        "review_state",
        "is_active",
        "created_at",
    )
    list_filter = (
        ("brand", RelatedDropdownFilter),
        ("source_type", ChoicesDropdownFilter),
        ("package_format", ChoicesDropdownFilter),
        "is_verified",
        "is_active",
        "is_password_protected",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "original_file_name",
        "brand__name",
        "model__name",
        "variant__name",
        "chipset",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
        "id",
        "source_type",
        "stored_file_path",
        "external_url",
        "external_link",
        "file_size",
        "file_hash",
        "is_verified",
        "reviewed_by",
        "reviewed_at",
        "uploader",
        "encrypted_password",
        "download_count",
        "view_count",
    )
    autocomplete_fields = ("brand", "model", "variant")
    fieldsets = (
        (
            _("Source"),
            {
                "fields": (
                    "id",
                    "original_file_name",
                    "source_type",
                    "stored_file_path",
                    "external_url",
                    "external_link",
                    "file_size",
                    "file_hash",
                    "uploader",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Device and build"),
            {
                "fields": (
                    "brand",
                    "model",
                    "variant",
                    "chipset",
                    "package_format",
                    "android_version",
                    "security_patch",
                    "build_date",
                    "build_number",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Review and availability"),
            {
                "fields": ("is_verified", "reviewed_by", "reviewed_at", "is_active"),
                "classes": ["tab"],
            },
        ),
        (
            _("Technical"),
            {
                "fields": (
                    "partitions",
                    "metadata",
                    "is_password_protected",
                    "encrypted_password",
                    "download_count",
                    "view_count",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Timestamps"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Review"), ordering="is_verified", label=BADGE_STATE)
    def review_state(self, obj: Any) -> str:
        return "verified" if obj.is_verified else "unreviewed"

    @display(description=_("External download"))
    def external_link(self, obj: Any) -> SafeString | str:
        return external_link(obj.external_url)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Any]:
        return (
            super()
            .get_queryset(request)
            .select_related("brand", "model", "variant", "uploader")
        )

    def _set_active(
        self, request: HttpRequest, queryset: QuerySet[Any], active: bool, label: str
    ) -> None:
        count = 0
        for firmware in queryset:
            firmware.is_active = active
            firmware.save(update_fields=["is_active", "updated_at"])
            count += 1
        self.message_user(request, f"{label} {count} firmware records.")

    @action(description="Make selected firmware available", permissions=["change"])
    def activate_selected(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        self._set_active(request, queryset, True, "Activated")

    @action(description="Hide selected firmware from downloads", permissions=["change"])
    def deactivate_selected(
        self, request: HttpRequest, queryset: QuerySet[Any]
    ) -> None:
        self._set_active(request, queryset, False, "Deactivated")


@register(OfficialFirmware)
class OfficialFirmwareAdmin(BaseFirmwareAdmin):
    pass


@register(EngineeringFirmware)
class EngineeringFirmwareAdmin(BaseFirmwareAdmin):
    pass


@register(ReadbackFirmware)
class ReadbackFirmwareAdmin(BaseFirmwareAdmin):
    pass


@register(ModifiedFirmware)
class ModifiedFirmwareAdmin(BaseFirmwareAdmin):
    pass


@register(OtherFirmware)
class OtherFirmwareAdmin(BaseFirmwareAdmin):
    pass


@register(UnclassifiedFirmware)
class UnclassifiedFirmwareAdmin(BaseFirmwareAdmin):
    pass


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------


class ReleaseNoteInline(TabularInline):
    model = ReleaseNote
    extra = 1
    tab = True
    ordering = ("id",)


@register(ChangelogEntry)
class ChangelogEntryAdmin(ModelAdmin):
    list_display = ("version", "title", "release_date", "is_published")
    list_filter = ("is_published", ("release_date", RangeDateFilter))
    search_fields = ("version", "title")
    inlines = (ReleaseNoteInline,)


@register(FirmwareDiff)
class FirmwareDiffAdmin(ModelAdmin):
    list_display = ("old_firmware", "new_firmware", "generated_at")
    readonly_fields = ("generated_at",)
    raw_id_fields = ("old_firmware", "new_firmware")


# ---------------------------------------------------------------------------
# GSMArena device synchronisation
# ---------------------------------------------------------------------------


@register(GSMArenaDevice)
class GSMArenaDeviceAdmin(ModelAdmin):
    list_display = (
        "gsmarena_id",
        "brand",
        "model_name",
        "review_badge",
        "last_synced_at",
    )
    list_filter = (
        ("review_status", ChoicesDropdownFilter),
        ("last_synced_at", RangeDateTimeFilter),
    )
    search_fields = ("gsmarena_id", "brand", "model_name")
    readonly_fields = ("last_synced_at", "reviewed_at", "source_link", "image_link")
    raw_id_fields = ("local_device", "reviewed_by")

    @display(description=_("Review"), ordering="review_status", label=BADGE_STATE)
    def review_badge(self, obj: GSMArenaDevice) -> str:
        return obj.review_status

    @display(description=_("GSMArena page"))
    def source_link(self, obj: GSMArenaDevice) -> SafeString | str:
        return external_link(obj.url)

    @display(description=_("Image URL"))
    def image_link(self, obj: GSMArenaDevice) -> SafeString | str:
        return external_link(obj.image_url)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@register(TrustedTester)
class TrustedTesterAdmin(ModelAdmin):
    list_display = ("user", "fw_count", "avg_rating", "is_active", "approved_at")
    list_filter = ("is_active", ("approved_at", RangeDateTimeFilter))
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)


class TestResultInline(TabularInline):
    model = TestResult
    extra = 0
    tab = True
    ordering = ("id",)


@register(VerificationReport)
class VerificationReportAdmin(ModelAdmin):
    list_display = (
        "pk",
        "firmware",
        "tester",
        "verdict_badge",
        "status_badge",
        "created_at",
    )
    list_filter = (
        ("verdict", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("tester__user__email",)
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("tester", "device")
    inlines = (TestResultInline,)

    @display(description=_("Verdict"), ordering="verdict", label=BADGE_STATE)
    def verdict_badge(self, obj: VerificationReport) -> str:
        return obj.verdict

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: VerificationReport) -> str:
        return obj.status


@register(TestResult)
class TestResultAdmin(ModelAdmin):
    list_display = ("report", "test_name", "result_badge")
    list_filter = (("result", ChoicesDropdownFilter),)
    search_fields = ("test_name",)
    raw_id_fields = ("report",)

    @display(description=_("Result"), ordering="result", label=BADGE_STATE)
    def result_badge(self, obj: TestResult) -> str:
        return obj.result


@register(VerificationCredit)
class VerificationCreditAdmin(ModelAdmin):
    list_display = ("user", "firmware", "credits_earned", "created_at")
    list_filter = (("created_at", RangeDateTimeFilter),)
    readonly_fields = ("created_at",)
    raw_id_fields = ("user", "report")


# ---------------------------------------------------------------------------
# Download links
# ---------------------------------------------------------------------------


@register(DownloadToken)
class DownloadTokenAdmin(NoAddModelAdmin):
    list_display = (
        "token_short",
        "firmware",
        "user",
        "status_badge",
        "ad_gate_completed",
        "expires_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        "ad_gate_required",
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email",)
    exclude = ("token",)
    readonly_fields = (
        "content_type",
        "object_id",
        "user",
        "ad_gate_required",
        "ad_gate_completed",
        "ip",
        "status",
        "expires_at",
        "created_at",
        "used_at",
    )
    actions = ("revoke_selected",)

    @display(description=_("Token"))
    def token_short(self, obj: DownloadToken) -> str:
        return f"{obj.token[:12]}…"

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: DownloadToken) -> str:
        return obj.status

    @action(description=_("Revoke selected active tokens"), permissions=["change"])
    def revoke_selected(
        self, request: HttpRequest, queryset: QuerySet[DownloadToken]
    ) -> None:
        count = queryset.filter(status=DownloadToken.Status.ACTIVE).update(
            status=DownloadToken.Status.REVOKED
        )
        self.message_user(request, _("%(count)d token(s) revoked.") % {"count": count})


@register(DownloadSession)
class DownloadSessionAdmin(ReadOnlyModelAdmin):
    list_display = (
        "pk",
        "token",
        "user",
        "status_badge",
        "bytes_delivered",
        "started_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("started_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("token", "user")

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: DownloadSession) -> str:
        return obj.status


@register(AdGateLog)
class AdGateLogAdmin(ReadOnlyModelAdmin):
    list_display = (
        "token",
        "session",
        "ad_type",
        "watched_seconds",
        "completed",
        "credits_earned",
    )
    list_filter = (
        ("ad_type", ChoicesDropdownFilter),
        "completed",
        ("created_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("token", "session")


@register(HotlinkBlock)
class HotlinkBlockAdmin(ModelAdmin):
    list_display = ("domain", "is_active", "blocked_count", "created_at")
    list_filter = ("is_active",)
    search_fields = ("domain",)
    readonly_fields = ("blocked_count", "created_at", "updated_at")


# ---------------------------------------------------------------------------
# Flashing tools and guides
# ---------------------------------------------------------------------------


@register(FlashingToolCategory)
class FlashingToolCategoryAdmin(ModelAdmin):
    list_display = ("name", "slug", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}


@register(FlashingTool)
class FlashingToolAdmin(ModelAdmin):
    list_display = (
        "name",
        "category",
        "tool_type",
        "platform",
        "risk_badge",
        "is_active",
        "is_featured",
    )
    list_filter = (
        ("category", RelatedDropdownFilter),
        ("tool_type", ChoicesDropdownFilter),
        ("platform", ChoicesDropdownFilter),
        ("risk_level", ChoicesDropdownFilter),
        "is_active",
        "is_featured",
    )
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    filter_horizontal = ("supported_brands", "chipsets")
    readonly_fields = (
        "created_at",
        "updated_at",
        "views_count",
        "downloads_count",
        "download_link",
        "official_link",
    )
    fieldsets = (
        (
            _("Overview"),
            {
                "fields": (
                    "name",
                    "slug",
                    "category",
                    "tool_type",
                    "platform",
                    "risk_level",
                    "description",
                    "hero_image",
                    "is_active",
                    "is_featured",
                    "is_free",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Links and version"),
            {
                "fields": (
                    "download_url",
                    "download_link",
                    "official_url",
                    "official_link",
                    "version",
                    "last_version_date",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Compatibility"),
            {
                "fields": (
                    "supported_brands",
                    "chipsets",
                    "features",
                    "requirements",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Instructions"),
            {"fields": ("instructions",), "classes": ["tab"]},
        ),
        (
            _("Statistics"),
            {
                "fields": (
                    "views_count",
                    "downloads_count",
                    "created_at",
                    "updated_at",
                ),
                "classes": ["tab"],
            },
        ),
    )

    @display(description=_("Risk"), ordering="risk_level", label=BADGE_STATE)
    def risk_badge(self, obj: FlashingTool) -> str:
        return obj.risk_level

    @display(description=_("Download URL"))
    def download_link(self, obj: FlashingTool) -> SafeString | str:
        return external_link(obj.download_url)

    @display(description=_("Official site"))
    def official_link(self, obj: FlashingTool) -> SafeString | str:
        return external_link(obj.official_url)

    def get_queryset(self, request: HttpRequest) -> QuerySet[FlashingTool]:
        return super().get_queryset(request).select_related("category")


@register(FlashingGuideTemplate)
class FlashingGuideTemplateAdmin(ModelAdmin):
    list_display = (
        "title_template",
        "guide_type",
        "brand",
        "auto_generate",
        "submit_for_review",
        "is_active",
        "generated_count",
    )
    list_filter = (
        ("guide_type", ChoicesDropdownFilter),
        ("brand", RelatedDropdownFilter),
        "auto_generate",
        "submit_for_review",
        "is_active",
    )
    search_fields = ("title_template",)
    readonly_fields = ("generated_count", "created_at", "updated_at")
    autocomplete_fields = ("brand",)
    filter_horizontal = ("recommended_tools",)


@register(GeneratedFlashingGuide)
class GeneratedFlashingGuideAdmin(ReadOnlyModelAdmin):
    list_display = ("template", "brand", "model", "created_at")
    list_filter = (
        ("brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("template", "post", "brand", "model")


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


@register(FirmwareView)
class FirmwareViewAdmin(ReadOnlyModelAdmin):
    list_display = ("content_type", "object_id", "user", "viewed_at")
    list_filter = (
        ("content_type", RelatedDropdownFilter),
        ("viewed_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("user",)


@register(FirmwareDownloadAttempt)
class FirmwareDownloadAttemptAdmin(ReadOnlyModelAdmin):
    list_display = ("content_type", "object_id", "user", "status_badge", "initiated_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("initiated_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("user",)

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: FirmwareDownloadAttempt) -> str:
        return obj.status


@register(FirmwareRequest)
class FirmwareRequestAdmin(ModelAdmin):
    list_display = (
        "brand",
        "model",
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
    raw_id_fields = ("brand", "model", "requested_by", "fulfilled_firmware_ct")
    readonly_fields = ("request_count", "created_at", "last_requested_at")

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: FirmwareRequest) -> str:
        return obj.status


@register(FirmwareStats)
class FirmwareStatsAdmin(ReadOnlyModelAdmin):
    list_display = (
        "content_type",
        "object_id",
        "date",
        "view_count",
        "successful_downloads",
    )
    list_filter = (("date", RangeDateFilter),)
