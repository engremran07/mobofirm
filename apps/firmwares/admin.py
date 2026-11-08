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
from django.db.models import Count, QuerySet
from django.db.models import Model as DjangoModel
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import ActionForm, BaseDialogForm
from unfold.widgets import (
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
)

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

from .catalog_requests import (
    approve_brand_request,
    approve_chipset_request,
    approve_model_request,
    approve_variant_request,
    decide_firmware_request,
    reject_catalog_request,
)
from .constants import MAIN_CATEGORIES
from .models import (
    AccessTier,
    AdGateLog,
    AutomatedBlogLink,
    Brand,
    BrandCreationRequest,
    ChangelogEntry,
    Chipset,
    ChipsetCreationRequest,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    FirmwareDiff,
    FlashingGuideTemplate,
    FlashingTool,
    FlashingToolCategory,
    GeneratedFlashingGuide,
    HotlinkBlock,
    Model,
    ModelCreationRequest,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    PendingFirmware,
    ReadbackFirmware,
    ReleaseNote,
    ServedDownload,
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
    FirmwareRequestSupport,
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

# Concrete firmware category tables (each registered below with its own admin).
FIRMWARE_CATEGORY_MODELS = (
    OfficialFirmware,
    EngineeringFirmware,
    ReadbackFirmware,
    ModifiedFirmware,
    OtherFirmware,
    UnclassifiedFirmware,
)

# Access tier -> Unfold badge colour.
BADGE_TIER: Mapping[str, str] = {
    "free": "success",
    "paid": "warning",
    "featured": "info",
}


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
        "description_status",
        "created_at",
    )
    list_filter = ("is_featured", ("created_at", RangeDateTimeFilter))
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at", "website_link", "logo_link")
    ordering = ("name",)
    inlines = (BrandModelInline,)
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
    def description_status(self, obj: Brand) -> str:
        return "complete" if obj.description else "incomplete"

    @display(description=_("Website"))
    def website_link(self, obj: Brand) -> SafeString | str:
        return external_link(obj.website_url)

    @display(description=_("Logo URL"))
    def logo_link(self, obj: Brand) -> SafeString | str:
        return external_link(obj.logo_url)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Brand]:
        return super().get_queryset(request).annotate(models_total=Count("models"))


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
    readonly_fields = (
        "created_at",
        "updated_at",
        "image_link",
        "firmware_summary",
        "pending_uploads",
        "blog_post_link",
    )
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
            _("Related"),
            {
                "fields": ("firmware_summary", "pending_uploads", "blog_post_link"),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Variants"), ordering="variants_total")
    def variant_count(self, obj: Model) -> int:
        return int(getattr(obj, "variants_total", 0))

    @display(description=_("Firmware by category"))
    def firmware_summary(self, obj: Model) -> SafeString | str:
        if not obj.pk:
            return "—"
        rows = []
        for firmware_model in FIRMWARE_CATEGORY_MODELS:
            count = firmware_model.objects.filter(model=obj).count()
            if count:
                opts = firmware_model._meta
                url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
                rows.append(
                    (f"{url}?model__id__exact={obj.pk}", opts.verbose_name, count)
                )
        if not rows:
            return str(_("No firmware yet."))
        return format_html_join(
            " · ",
            '<a class="text-primary-600 underline dark:text-primary-500" '
            'href="{}">{}: {}</a>',
            rows,
        )

    @display(description=_("Pending uploads"))
    def pending_uploads(self, obj: Model) -> SafeString | str:
        if not obj.pk:
            return "—"
        count = PendingFirmware.objects.filter(
            uploaded_model=obj, admin_decision="pending"
        ).count()
        if not count:
            return str(_("Nothing waiting for review."))
        url = reverse("admin:firmwares_pendingfirmware_changelist")
        return format_html(
            '<a class="text-primary-600 underline dark:text-primary-500" '
            'href="{}?uploaded_model__id__exact={}&admin_decision__exact=pending">'
            "{}</a>",
            url,
            obj.pk,
            _("%(count)s waiting for review") % {"count": count},
        )

    @display(description=_("Automated blog post"))
    def blog_post_link(self, obj: Model) -> SafeString | str:
        link = (
            AutomatedBlogLink.objects.select_related("post").filter(model=obj).first()
            if obj.pk
            else None
        )
        if link is None:
            return str(_("No automated blog post yet."))
        return format_html(
            '<a class="text-primary-600 underline dark:text-primary-500" '
            'href="{}">{}</a>',
            reverse("admin:blog_post_change", args=[link.post_id]),
            link.post.title,
        )

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


class VariantAdminForm(forms.ModelForm):
    """Brand first, then only that brand's models: the model must belong to it."""

    brand = forms.ModelChoiceField(
        queryset=Brand.objects.order_by("name"),
        label=_("Brand"),
        help_text=_("Choose the brand first; the model list shows only its models."),
        widget=UnfoldAdminSelectWidget,
    )

    class Meta:
        model = Variant
        fields = "__all__"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        brand_id = self._selected_brand_id()
        self.fields["brand"].widget.attrs.update(
            {
                "hx-get": reverse("admin:firmwares_variant_model_options"),
                "hx-trigger": "change",
                "hx-target": "#id_model",
                "hx-swap": "innerHTML",
            }
        )
        model_field = self.fields["model"]
        if isinstance(model_field, forms.ModelChoiceField):
            model_field.queryset = (
                Model.objects.filter(brand_id=brand_id).order_by("name")
                if brand_id
                else Model.objects.none()
            )
        if brand_id and "brand" not in self.initial:
            self.initial["brand"] = brand_id

    def _selected_brand_id(self) -> int | None:
        raw: object = self.data.get(self.add_prefix("brand")) if self.is_bound else None
        if raw in (None, ""):
            raw = (
                self.instance.model.brand_id
                if self.instance.model_id
                else self.initial.get("brand")
            )
        text = str(raw or "")
        return int(text) if text.isdigit() else None

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        brand, model = cleaned.get("brand"), cleaned.get("model")
        if brand and model and model.brand_id != brand.pk:
            self.add_error(
                "model", _("This model does not belong to the chosen brand.")
            )
        return cleaned


@register(Variant)
class VariantAdmin(ModelAdmin):
    form = VariantAdminForm
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
    autocomplete_fields = ("chipset_ref",)
    ordering = ("model__brand__name", "model__name", "name")
    fieldsets = (
        (
            _("Identification"),
            {
                "fields": (
                    "brand",
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

    def get_urls(self) -> list[URLPattern]:
        custom = path(
            "model-options/",
            self.admin_site.admin_view(self.model_options),
            name="firmwares_variant_model_options",
        )
        return [custom, *super().get_urls()]

    def model_options(self, request: HttpRequest) -> HttpResponse:
        """``<option>`` list of one brand's models for the dependent model select."""
        if not (
            self.has_add_permission(request) or self.has_change_permission(request)
        ):
            raise PermissionDenied
        raw = request.GET.get("brand", "")
        models = (
            Model.objects.filter(brand_id=int(raw)).order_by("name")
            if raw.isdigit()
            else Model.objects.none()
        )
        options = format_html_join(
            "", '<option value="{}">{}</option>', ((m.pk, m.name) for m in models)
        )
        return HttpResponse(
            format_html('<option value="">---------</option>{}', options)
        )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Variant]:
        return (
            super().get_queryset(request).select_related("model__brand", "chipset_ref")
        )


# ---------------------------------------------------------------------------
# Catalogue creation requests
# ---------------------------------------------------------------------------


class CreationRequestForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


class ApprovalNoteForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Note to the requester (optional)"),
        required=False,
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


class ReviewOnlyAdmin(NoAddModelAdmin):
    """Requests come from end users; staff only review them, never edit or add."""

    def get_readonly_fields(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> Sequence[str]:
        return [field.name for field in self.model._meta.fields]


class CreationRequestAdmin(ReviewOnlyAdmin):
    """Catalogue requests: staff approve or reject with a recorded reason."""

    list_filter: Sequence[tuple[str, Any]] = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    list_select_related = ("requested_by", "reviewed_by")
    actions: Sequence[str] = ("approve_requests",)
    actions_row: Sequence[str] = ("approve_request", "reject_request")
    actions_detail: Sequence[str] = ("approve_request", "reject_request")

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: Any) -> str:
        return str(obj.status)

    def _approve_one(self, request: HttpRequest, pending: Any, reason: str) -> bool:
        raise NotImplementedError

    def _approve_many(
        self, request: HttpRequest, queryset: QuerySet[Any]
    ) -> tuple[int, list[str]]:
        done, problems = 0, []
        for pending in queryset.filter(status="pending"):
            try:
                self._approve_one(request, pending, "")
            except (PermissionDenied, ValidationError) as error:
                detail = " ".join(getattr(error, "messages", [str(error)]))
                problems.append(f"{pending}: {detail}")
            else:
                done += 1
        return done, problems

    @action(description=_("Approve selected requests"), permissions=["change"])
    def approve_requests(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        done, problems = self._approve_many(request, queryset)
        self.message_user(
            request, _("%(count)d request(s) approved.") % {"count": done}
        )
        for problem in problems:
            self.message_user(request, problem, messages.ERROR)

    def _decide_one(
        self,
        request: HttpRequest,
        object_id: str,
        decide: Callable[[Any], str],
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        try:
            outcome = decide(target)
        except (PermissionDenied, ValidationError) as error:
            detail = " ".join(getattr(error, "messages", [str(error)]))
            self.message_user(request, detail, messages.ERROR)
        else:
            self.message_user(request, outcome)
        return htmx_redirect(return_url(request, change_url(self, target.pk)))

    @action(
        description=_("Approve and create"),
        icon="check_circle",
        permissions=["change"],
        url_path="approve-request",
        dialog=confirm_dialog(
            _("Approve this request"),
            _("Creates the catalogue record and notifies the requester."),
            ApprovalNoteForm,
            submit=_("Approve"),
        ),
    )
    def approve_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        reason = str(form.cleaned_data["reason"])

        def decide(target: Any) -> str:
            self._approve_one(request, target, reason)
            return str(_("Request approved."))

        return self._decide_one(request, object_id, decide)

    @action(
        description=_("Reject request"),
        icon="block",
        permissions=["change"],
        url_path="reject-request",
        dialog=confirm_dialog(
            _("Reject this request"),
            _("The reason is stored on the request and sent to the requester."),
            CreationRequestForm,
            submit=_("Reject"),
        ),
    )
    def reject_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        reason = str(form.cleaned_data["reason"])

        def decide(target: Any) -> str:
            reject_catalog_request(target, request.user, reason=reason)
            return str(_("Request rejected."))

        return self._decide_one(request, object_id, decide)


@register(BrandCreationRequest)
class BrandCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "status_badge", "requested_by", "reviewed_by", "created_at")
    search_fields = ("name", "requested_by__email")

    def _approve_one(self, request: HttpRequest, pending: Any, reason: str) -> bool:
        brand, _created = approve_brand_request(pending, request.user, reason=reason)
        logger.info("Brand request %s approved as brand %s", pending.pk, brand.pk)
        return True


@register(ModelCreationRequest)
class ModelCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "brand", "status_badge", "requested_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "brand__name", "requested_by__email")

    def _approve_one(self, request: HttpRequest, pending: Any, reason: str) -> bool:
        approve_model_request(pending, request.user, reason=reason)
        return True


@register(VariantCreationRequest)
class VariantCreationRequestAdmin(CreationRequestAdmin):
    list_display = ("name", "model", "status_badge", "requested_by", "created_at")
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("model__brand", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("name", "model__name", "model__brand__name", "requested_by__email")

    def _approve_one(self, request: HttpRequest, pending: Any, reason: str) -> bool:
        approve_variant_request(pending, request.user, reason=reason)
        return True


@register(ChipsetCreationRequest)
class ChipsetCreationRequestAdmin(CreationRequestAdmin):
    list_display = (
        "name",
        "vendor",
        "family",
        "status_badge",
        "requested_by",
        "created_at",
    )
    search_fields = ("name", "vendor", "family", "requested_by__email")

    def _approve_one(self, request: HttpRequest, pending: Any, reason: str) -> bool:
        approve_chipset_request(pending, request.user, reason=reason)
        return True


# ---------------------------------------------------------------------------
# Firmware moderation
# ---------------------------------------------------------------------------


TIER_CHOICES = [("", _("As requested by the uploader")), *AccessTier.choices]


def _price_field(label: str) -> forms.DecimalField:
    return forms.DecimalField(
        label=label,
        required=False,
        min_value=0,
        max_digits=10,
        decimal_places=2,
        widget=UnfoldAdminTextInputWidget,
    )


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
    access_tier = forms.ChoiceField(
        choices=TIER_CHOICES,
        required=False,
        label=_("Access tier"),
        help_text=_("Only files from approved sellers can be paid or featured."),
        widget=UnfoldAdminSelectWidget,
    )
    approved_price = _price_field(str(_("Approved price (USD, paid files only)")))
    notes = forms.CharField(
        required=False,
        max_length=1000,
        label=_("Decision notes"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        tier, price = cleaned.get("access_tier"), cleaned.get("approved_price")
        if tier == AccessTier.PAID and not price:
            self.add_error("approved_price", _("A paid file needs an approved price."))
        if tier in (AccessTier.FREE, AccessTier.FEATURED) and price:
            self.add_error("approved_price", _("Only paid files have a price."))
        return cleaned


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
        "tier_badge",
        "reviewed_by",
        "created_at",
    )
    list_filter = (
        ("admin_decision", ChoicesDropdownFilter),
        ("access_tier", ChoicesDropdownFilter),
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
        "access_tier",
        "proposed_price",
        "approved_price",
        "price_reviewed_by",
        "price_reviewed_at",
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
            _("Access and price"),
            {
                "fields": (
                    "access_tier",
                    "proposed_price",
                    "approved_price",
                    "price_reviewed_by",
                    "price_reviewed_at",
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

    @display(description=_("Tier"), ordering="access_tier", label=BADGE_TIER)
    def tier_badge(self, obj: PendingFirmware) -> str:
        return obj.access_tier

    @display(description=_("External download"))
    def external_link(self, obj: PendingFirmware) -> SafeString | str:
        return external_link(obj.external_url)

    def _review(
        self,
        request: HttpRequest,
        queryset: QuerySet[PendingFirmware],
        handler: ReviewHandler,
        done_label: str,
        **kwargs: Any,
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
        **kwargs: Any,
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
            access_tier=str(form.cleaned_data["access_tier"]) or None,
            approved_price=form.cleaned_data["approved_price"],
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
        "tier_badge",
        "is_active",
        "created_at",
    )
    list_filter = (
        ("brand", RelatedDropdownFilter),
        ("access_tier", ChoicesDropdownFilter),
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
        "proposed_price",
        "price_reviewed_by",
        "price_reviewed_at",
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
            _("Access and price"),
            {
                "fields": (
                    "access_tier",
                    "proposed_price",
                    "approved_price",
                    "price_reviewed_by",
                    "price_reviewed_at",
                ),
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

    @display(description=_("Tier"), ordering="access_tier", label=BADGE_TIER)
    def tier_badge(self, obj: Any) -> str:
        return str(obj.access_tier)

    @display(description=_("External download"))
    def external_link(self, obj: Any) -> SafeString | str:
        return external_link(obj.external_url)

    def save_model(
        self, request: HttpRequest, obj: Any, form: Any, change: bool
    ) -> None:
        """A staff edit of the tier or price records who reviewed it and when."""
        if change and {"access_tier", "approved_price"} & set(form.changed_data):
            obj.price_reviewed_by = request.user
            obj.price_reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)

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


@register(ServedDownload)
class ServedDownloadAdmin(ReadOnlyModelAdmin):
    list_display = (
        "user",
        "content_type",
        "object_id",
        "tier_badge",
        "paid_amount",
        "charged_quota",
        "served_at",
    )
    list_filter = (
        ("tier", ChoicesDropdownFilter),
        "charged_quota",
        ("served_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)

    @display(description=_("Tier"), ordering="tier", label=BADGE_TIER)
    def tier_badge(self, obj: ServedDownload) -> str:
        return obj.tier


class FirmwareRequestSupportInline(TabularInline):
    model = FirmwareRequestSupport
    fields = ("user", "created_at")
    readonly_fields = ("user", "created_at")
    can_delete = False

    def has_add_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(FirmwareRequest)
class FirmwareRequestAdmin(ReviewOnlyAdmin):
    actions_row = ("start_work", "fulfil_request", "reject_firmware_request")
    actions_detail = ("start_work", "fulfil_request", "reject_firmware_request")
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
    list_select_related = ("brand", "model")

    @display(description=_("Status"), ordering="status", label=BADGE_STATE)
    def status_badge(self, obj: FirmwareRequest) -> str:
        return obj.status

    def _set_status(
        self, request: HttpRequest, object_id: str, status: str, reason: str, done: str
    ) -> HttpResponse:
        target = self.get_object(request, unquote(object_id))
        if target is None:
            raise Http404
        try:
            decide_firmware_request(target, request.user, status=status, reason=reason)
        except (PermissionDenied, ValidationError) as error:
            detail = " ".join(getattr(error, "messages", [str(error)]))
            self.message_user(request, detail, messages.ERROR)
        else:
            self.message_user(request, done)
        return htmx_redirect(return_url(request, change_url(self, target.pk)))

    @action(
        description=_("Mark in progress"),
        icon="pending",
        permissions=["change"],
        url_path="start-work",
        dialog=confirm_dialog(
            _("Mark this request in progress"),
            _("Requesters see that the file is being worked on."),
            submit=_("Mark in progress"),
        ),
    )
    def start_work(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._set_status(
            request, object_id, "in_progress", "", str(_("Marked in progress."))
        )

    @action(
        description=_("Mark fulfilled"),
        icon="check_circle",
        permissions=["change"],
        url_path="fulfil-request",
        dialog=confirm_dialog(
            _("The firmware has been added"),
            _("Everyone who asked for it is notified."),
            ApprovalNoteForm,
            submit=_("Mark fulfilled"),
        ),
    )
    def fulfil_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._set_status(
            request,
            object_id,
            "fulfilled",
            str(form.cleaned_data["reason"]),
            str(_("Request marked fulfilled.")),
        )

    @action(
        description=_("Reject request"),
        icon="block",
        permissions=["change"],
        url_path="reject-firmware-request",
        dialog=confirm_dialog(
            _("Reject this firmware request"),
            _("The reason is stored and sent to everyone who asked."),
            CreationRequestForm,
            submit=_("Reject"),
        ),
    )
    def reject_firmware_request(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._set_status(
            request,
            object_id,
            "rejected",
            str(form.cleaned_data["reason"]),
            str(_("Request rejected.")),
        )


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
