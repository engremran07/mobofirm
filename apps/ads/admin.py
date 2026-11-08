"""Ads admin: placements, campaigns, networks, rewarded ads, affiliate catalogue and scans."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q, QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.enums import ActionVariant

from apps.ads import rewarded
from apps.ads.services import markup, placement_manager, scanner
from apps.core.admin_base import (
    BADGE_APPROVAL,
    ConfirmForm,
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

from .models import (
    AdCreative,
    AdEvent,
    AdNetwork,
    AdPlacement,
    AdsSettings,
    AdUnit,
    AffiliateClick,
    AffiliateLink,
    AffiliateProduct,
    AffiliateProductCategory,
    AffiliateProductMatch,
    AffiliateProvider,
    AffiliateSource,
    AutoAdsScanResult,
    Campaign,
    PlacementAssignment,
    RewardedAdConfig,
    RewardedAdGrant,
    RewardedAdView,
    ScanDiscovery,
    TemplateAdExclusion,
)

SOFT_DELETE_FIELDS = ("is_deleted", "deleted_at", "deleted_by")
AUDIT_FIELDSET = (
    _("Audit"),
    {"classes": ("tab",), "fields": ("created_by", "updated_by")},
)
BADGE_REVIEW: dict[str, str] = {
    **BADGE_APPROVAL,
    "partial": "info",
    "excluded": "info",
}
BADGE_VIEW_STATUS: dict[str, str] = {
    "started": "info",
    "completed": "success",
    "failed": "danger",
    "skipped": "warning",
    "expired": "warning",
    "rejected": "danger",
    "error": "danger",
}
BADGE_LIVE_STATE: dict[str, str] = {
    "live": "success",
    "setup": "warning",
    "off": "danger",
}
BADGE_STATE: dict[str, str] = {
    "enabled": "success",
    "disabled": "danger",
    "locked": "warning",
}


class AuditedModelAdmin(ModelAdmin):
    """Soft-deletable, audited ad records: hidden delete bookkeeping, stamped authors."""

    exclude = SOFT_DELETE_FIELDS
    readonly_fields: Sequence[str] = ("created_by", "updated_by")

    def save_model(
        self, request: HttpRequest, obj: Any, form: ModelForm, change: bool
    ) -> None:
        if not change and getattr(obj, "created_by_id", None) is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


def _on_off(enabled: bool) -> tuple[str, str]:
    return (
        ("enabled", str(_("Enabled"))) if enabled else ("disabled", str(_("Disabled")))
    )


# --------------------------------------------------------------------------
# Placements, campaigns and creatives
# --------------------------------------------------------------------------
class PlacementAssignmentInline(TabularInline):
    """Creatives that fill this placement; weight sets how often each is shown."""

    model = PlacementAssignment
    fk_name = "placement"
    extra = 0
    tab = True
    fields = ("creative", "weight", "is_enabled", "is_active", "locked")
    raw_id_fields = ("creative",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlacementAssignment]:
        return super().get_queryset(request).filter(is_deleted=False)


class AdPlacementForm(forms.ModelForm):
    """A zone is only valid where the template (or the shared layout) declares it."""

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        zone = cleaned.get("zone")
        template_path = (cleaned.get("template_reference") or "").strip()
        if zone:
            declared = (
                placement_manager.declared_zones(template_path)
                if template_path
                else placement_manager.site_wide_zones()
            )
            if zone not in declared:
                self.add_error(
                    "zone",
                    _(
                        "%(where)s does not declare this zone, so an ad would never "
                        "show. Pick a zone it declares or change the template."
                    )
                    % {"where": template_path or _("The shared layout")},
                )
        return cleaned


@admin.register(AdPlacement)
class AdPlacementAdmin(AuditedModelAdmin):
    form = AdPlacementForm
    list_display = (
        "name",
        "zone",
        "template_reference",
        "device_target",
        "placement_state",
        "live_creatives",
        "updated_at",
    )
    list_filter = (
        ("zone", ChoicesDropdownFilter),
        ("device_target", ChoicesDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("locked", BooleanRadioFilter),
        "context",
    )
    search_fields = (
        "name",
        "slug",
        "code",
        "context",
        "page_context",
        "template_reference",
    )
    inlines = (PlacementAssignmentInline,)
    raw_id_fields = ("fallback_creative",)
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    actions = ("switch_on", "switch_off")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "code",
                    "slug",
                    "description",
                    ("is_enabled", "is_active", "locked"),
                )
            },
        ),
        (
            _("Where it shows"),
            {
                "classes": ("tab",),
                "description": _(
                    "Pages declare layout zones. Choose the zone and, to limit it to one "
                    "page, its template path (empty = every page that declares the zone). "
                    "The Placement manager lists the templates and their zones."
                ),
                "fields": ("zone", "template_reference", "device_target"),
            },
        ),
        (
            _("Content"),
            {
                "classes": ("tab",),
                "fields": (
                    "allowed_types",
                    "allowed_sizes",
                    "fallback_creative",
                    "context",
                    "page_context",
                ),
            },
        ),
        AUDIT_FIELDSET,
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[AdPlacement]:
        return (
            super()
            .get_queryset(request)
            .filter(is_deleted=False)
            .annotate(
                live_creative_count=Count(
                    "assignments",
                    filter=Q(
                        assignments__is_enabled=True,
                        assignments__is_active=True,
                        assignments__is_deleted=False,
                    ),
                )
            )
        )

    @display(description=_("State"), ordering="is_enabled", label=BADGE_STATE)
    def placement_state(self, obj: AdPlacement) -> tuple[str, str]:
        if obj.locked:
            return "locked", str(_("Locked"))
        return _on_off(obj.is_enabled and obj.is_active)

    @display(description=_("Creatives"), ordering="live_creative_count")
    def live_creatives(self, obj: Any) -> int:
        return int(getattr(obj, "live_creative_count", 0))

    @action(description=_("Switch on selected placements"), permissions=["change"])
    def switch_on(self, request: HttpRequest, queryset: QuerySet[AdPlacement]) -> None:
        count = queryset.update(is_enabled=True)
        self.message_user(request, _("%(n)d placements switched on.") % {"n": count})

    @action(description=_("Switch off selected placements"), permissions=["change"])
    def switch_off(self, request: HttpRequest, queryset: QuerySet[AdPlacement]) -> None:
        count = queryset.update(is_enabled=False)
        self.message_user(request, _("%(n)d placements switched off.") % {"n": count})


@admin.register(Campaign)
class CampaignAdmin(AuditedModelAdmin):
    list_display = (
        "name",
        "type",
        "campaign_state",
        "priority",
        "weight",
        "daily_cap",
        "total_cap",
        "start_at",
        "end_at",
    )
    list_filter = (
        ("type", ChoicesDropdownFilter),
        ("is_active", BooleanRadioFilter),
        "ad_network",
        ("start_at", RangeDateTimeFilter),
    )
    search_fields = ("name",)
    raw_id_fields = ("promotion",)
    actions = ("activate", "pause")
    fieldsets = (
        (None, {"fields": ("name", "type", "ad_network", "is_active", "locked")}),
        (
            _("Schedule and limits"),
            {
                "classes": ("tab",),
                "fields": (
                    "start_at",
                    "end_at",
                    "daily_cap",
                    "total_cap",
                    "budget",
                    "priority",
                    "weight",
                ),
            },
        ),
        (
            _("Targeting and offer"),
            {"classes": ("tab",), "fields": ("targeting_rules", "promotion")},
        ),
        AUDIT_FIELDSET,
    )

    @display(description=_("State"), ordering="is_active", label=BADGE_LIVE_STATE)
    def campaign_state(self, obj: Campaign) -> tuple[str, str]:
        if obj.is_live():
            return "live", str(_("Live"))
        return (
            ("setup", str(_("Scheduled")))
            if obj.is_active
            else (
                "off",
                str(_("Paused")),
            )
        )

    @action(description=_("Activate selected campaigns"), permissions=["change"])
    def activate(self, request: HttpRequest, queryset: QuerySet[Campaign]) -> None:
        self.message_user(
            request,
            _("%(n)d campaigns activated.") % {"n": queryset.update(is_active=True)},
        )

    @action(description=_("Pause selected campaigns"), permissions=["change"])
    def pause(self, request: HttpRequest, queryset: QuerySet[Campaign]) -> None:
        self.message_user(
            request,
            _("%(n)d campaigns paused.") % {"n": queryset.update(is_active=False)},
        )


class AdCreativeForm(forms.ModelForm):
    """Each creative type needs its own content, and markup must pass the allowlist."""

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean() or {}
        kind = cleaned.get("creative_type")
        html = cleaned.get("html") or cleaned.get("html_code") or ""
        script = cleaned.get("script_code") or ""
        if kind == "banner" and not cleaned.get("image_url"):
            self.add_error("image_url", _("A banner needs an image URL."))
        if kind in {"html", "native"} and not html.strip():
            self.add_error("html", _("This creative type needs HTML."))
        if kind == "script" and not script.strip():
            self.add_error("script_code", _("A script creative needs script code."))
        for name, value in (
            ("html", cleaned.get("html")),
            ("html_code", cleaned.get("html_code")),
            ("script_code", script),
        ):
            if value and not markup.validate_ad_creative_code(value):
                self.add_error(
                    name,
                    _(
                        "Rejected: scripts must be https from an approved ad host, and "
                        "event handlers, style attributes and javascript: URLs are not allowed."
                    ),
                )
        return cleaned


@admin.register(AdCreative)
class AdCreativeAdmin(AuditedModelAdmin):
    form = AdCreativeForm
    list_display = (
        "name",
        "campaign",
        "creative_type",
        "is_active",
        "locked",
        "weight",
    )
    list_filter = (
        ("creative_type", ChoicesDropdownFilter),
        ("is_active", BooleanRadioFilter),
        ("locked", BooleanRadioFilter),
        ("campaign", RelatedDropdownFilter),
    )
    search_fields = ("name", "campaign__name")
    raw_id_fields = ("campaign",)
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "campaign",
                    "name",
                    "creative_type",
                    "weight",
                    ("is_enabled", "is_active", "locked"),
                )
            },
        ),
        (
            _("Banner"),
            {
                "classes": ("tab",),
                "fields": ("image_url", "click_url", "tracking_params"),
            },
        ),
        (
            _("HTML or script"),
            {"classes": ("tab",), "fields": ("html", "html_code", "script_code")},
        ),
        AUDIT_FIELDSET,
    )


@admin.register(PlacementAssignment)
class PlacementAssignmentAdmin(AuditedModelAdmin):
    list_display = (
        "placement",
        "placement_zone",
        "creative",
        "weight",
        "is_enabled",
        "is_active",
        "locked",
    )
    list_filter = (
        ("placement", RelatedDropdownFilter),
        ("creative", RelatedDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
        ("is_active", BooleanRadioFilter),
        ("locked", BooleanRadioFilter),
    )
    raw_id_fields = ("placement", "creative")

    @display(description=_("Zone"), ordering="placement__zone")
    def placement_zone(self, obj: PlacementAssignment) -> str:
        return obj.placement.get_zone_display() or "-"

    def get_queryset(self, request: HttpRequest) -> QuerySet[PlacementAssignment]:
        return (
            super()
            .get_queryset(request)
            .filter(is_deleted=False)
            .select_related("placement", "creative")
        )


@admin.register(AffiliateSource)
class AffiliateSourceAdmin(AuditedModelAdmin):
    list_display = ("name", "network", "base_url", "is_enabled", "locked", "updated_at")
    list_filter = (
        "network",
        ("is_enabled", BooleanRadioFilter),
        ("locked", BooleanRadioFilter),
    )
    search_fields = ("name", "network", "base_url")


@admin.register(AffiliateLink)
class AffiliateLinkAdmin(AuditedModelAdmin):
    list_display = (
        "name",
        "source",
        "affiliate_url",
        "is_active",
        "locked",
        "usage_count",
    )
    list_filter = (
        ("source", RelatedDropdownFilter),
        ("is_active", BooleanRadioFilter),
        ("locked", BooleanRadioFilter),
    )
    search_fields = ("name", "affiliate_url", "target_url", "source__name")
    raw_id_fields = ("source",)


@admin.register(AdEvent)
class AdEventAdmin(ReadOnlyModelAdmin):
    list_display = (
        "event_type",
        "campaign",
        "placement",
        "creative",
        "page_url",
        "created_at",
    )
    list_filter = (
        ("event_type", ChoicesDropdownFilter),
        ("campaign", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    readonly_fields = (
        "event_type",
        "campaign",
        "placement",
        "creative",
        "user",
        "request_meta",
        "page_url",
        "referrer_url",
        "user_agent",
        "session_id",
        "created_at",
    )
    date_hierarchy = "created_at"


# --------------------------------------------------------------------------
# Networks and units
# --------------------------------------------------------------------------
class AdUnitInline(TabularInline):
    model = AdUnit
    tab = True
    fields = (
        "name",
        "unit_id",
        "ad_format",
        "size_label",
        "is_enabled",
        "estimated_cpm",
    )
    exclude = SOFT_DELETE_FIELDS


@admin.register(AdNetwork)
class AdNetworkAdmin(AuditedModelAdmin):
    list_display = (
        "name",
        "network_type",
        "network_state",
        "priority",
        "supports_display",
        "supports_video",
        "supports_rewarded",
        "last_sync_at",
    )
    list_filter = (
        ("network_type", ChoicesDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
        ("supports_display", BooleanRadioFilter),
        ("supports_video", BooleanRadioFilter),
        ("supports_rewarded", BooleanRadioFilter),
    )
    search_fields = ("name", "publisher_id")
    inlines = (AdUnitInline,)
    readonly_fields = ("created_by", "updated_by", "last_sync_at", "sync_status")
    fieldsets = (
        (None, {"fields": ("name", "network_type", "is_enabled", "priority")}),
        (
            _("Authentication"),
            {"classes": ("tab",), "fields": ("publisher_id", "api_key", "api_secret")},
        ),
        (
            _("Scripts"),
            {"classes": ("tab",), "fields": ("header_script", "body_script")},
        ),
        (
            _("Capabilities"),
            {
                "classes": ("tab",),
                "fields": (
                    "supports_display",
                    "supports_native",
                    "supports_video",
                    "supports_rewarded",
                    "supports_auto_ads",
                    "revenue_share_percent",
                ),
            },
        ),
        (
            _("Advanced"),
            {"classes": ("tab",), "fields": ("config", "last_sync_at", "sync_status")},
        ),
        AUDIT_FIELDSET,
    )

    @display(description=_("State"), ordering="is_enabled", label=BADGE_STATE)
    def network_state(self, obj: AdNetwork) -> tuple[str, str]:
        return _on_off(obj.is_enabled)


@admin.register(AdUnit)
class AdUnitAdmin(AuditedModelAdmin):
    list_display = (
        "name",
        "network",
        "unit_id",
        "ad_format",
        "size_label",
        "is_enabled",
        "estimated_cpm",
        "fill_rate",
    )
    list_filter = (
        ("network", RelatedDropdownFilter),
        ("ad_format", ChoicesDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
    )
    search_fields = ("name", "unit_id", "network__name")
    raw_id_fields = ("network",)


# --------------------------------------------------------------------------
# Rewarded ads
# --------------------------------------------------------------------------
@admin.register(RewardedAdConfig)
class RewardedAdConfigAdmin(AuditedModelAdmin):
    """Disabled by default: a rewarded ad needs a placement before it can go live."""

    list_display = (
        "name",
        "reward_type",
        "reward_amount",
        "live_state",
        "cooldown_minutes",
        "daily_limit_per_user",
        "monthly_limit_per_user",
    )
    list_filter = (
        ("reward_type", ChoicesDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
        ("network", RelatedDropdownFilter),
    )
    search_fields = ("name", "reward_description")
    raw_id_fields = ("placement", "network", "ad_unit")
    readonly_fields = (*AuditedModelAdmin.readonly_fields, "availability")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "placement",
                    "network",
                    "ad_unit",
                    "is_enabled",
                    "availability",
                )
            },
        ),
        (
            _("Reward"),
            {
                "classes": ("tab",),
                "fields": (
                    "reward_type",
                    "reward_amount",
                    "reward_description",
                    "valid_hours",
                ),
            },
        ),
        (
            _("Limits"),
            {
                "classes": ("tab",),
                "fields": (
                    "min_watch_seconds",
                    "cooldown_minutes",
                    "daily_limit_per_user",
                    "monthly_limit_per_user",
                ),
            },
        ),
        AUDIT_FIELDSET,
    )

    @display(description=_("State"), label=BADGE_LIVE_STATE)
    def live_state(self, obj: RewardedAdConfig) -> tuple[str, str]:
        if not obj.is_enabled:
            return "off", str(_("Disabled"))
        if rewarded.unavailable_reason(obj):
            return "setup", str(_("Not configured"))
        return "live", str(_("Live"))

    @display(description=_("Availability to users"))
    def availability(self, obj: RewardedAdConfig) -> str:
        if obj.pk is None:
            return str(_("Save first."))
        return rewarded.unavailable_reason(obj) or str(
            _("Live: users can start this rewarded ad.")
        )


@admin.register(RewardedAdView)
class RewardedAdViewAdmin(ReadOnlyModelAdmin):
    """Every watch with its server-measured time and abuse flags."""

    list_display = (
        "user",
        "config",
        "status_badge",
        "reward_granted",
        "reward_type",
        "reward_amount",
        "watched_seconds",
        "is_flagged",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("reward_granted", BooleanRadioFilter),
        ("is_flagged", BooleanRadioFilter),
        ("config", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username")
    list_select_related = ("user", "config")
    readonly_fields = (
        "user",
        "config",
        "started_at",
        "expires_at",
        "last_heartbeat_at",
        "heartbeat_count",
        "accrued_ms",
        "early_attempts",
        "completed_at",
        "watch_duration_seconds",
        "status",
        "reward_granted",
        "reward_type",
        "reward_amount",
        "reward_note",
        "flags",
        "is_flagged",
        "ip_address",
        "user_agent",
        "page_url",
        "created_at",
    )
    exclude = ("token_digest",)

    @display(description=_("Status"), ordering="status", label=BADGE_VIEW_STATUS)
    def status_badge(self, obj: RewardedAdView) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @display(description=_("Watched (s)"), ordering="accrued_ms")
    def watched_seconds(self, obj: RewardedAdView) -> int:
        return obj.accrued_ms // 1000


@admin.register(RewardedAdGrant)
class RewardedAdGrantAdmin(ReadOnlyModelAdmin):
    """Extra downloads and ad-free periods earned by rewarded ads."""

    list_display = ("user", "kind", "amount", "expires_at", "created_at")
    list_filter = (("kind", ChoicesDropdownFilter), ("expires_at", RangeDateTimeFilter))
    search_fields = ("user__email", "user__username")
    list_select_related = ("user",)


# --------------------------------------------------------------------------
# Template scans: discoveries are reviewed through the scanner service
# --------------------------------------------------------------------------
@admin.register(AutoAdsScanResult)
class AutoAdsScanResultAdmin(NoAddModelAdmin):
    list_display = ("template_path", "score", "review_badge", "applied", "created_at")
    list_filter = (
        ("review_status", ChoicesDropdownFilter),
        ("applied", BooleanRadioFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("template_path", "ai_recommendation")
    readonly_fields = (
        "template_path",
        "suggested_placements",
        "content_analysis",
        "ai_recommendation",
        "score",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    )

    @display(description=_("Review"), ordering="review_status", label=BADGE_REVIEW)
    def review_badge(self, obj: AutoAdsScanResult) -> tuple[str, str]:
        return obj.review_status, obj.get_review_status_display()


@admin.register(ScanDiscovery)
class ScanDiscoveryAdmin(ModelAdmin):
    list_display = (
        "placement_code",
        "placement_name",
        "zone",
        "confidence",
        "status_badge",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = (("status", ChoicesDropdownFilter), "zone")
    search_fields = ("placement_code", "placement_name", "description")
    readonly_fields = (
        "scan_result",
        "status",
        "reviewed_by",
        "reviewed_at",
        "placement",
    )
    actions = ("approve_selected", "reject_selected")
    actions_row = ("approve", "reject")
    actions_detail = ("approve", "reject")

    @display(description=_("Status"), ordering="status", label=BADGE_APPROVAL)
    def status_badge(self, obj: ScanDiscovery) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @staticmethod
    def _count(
        decide: Callable[[int, Any], dict[str, Any]],
        queryset: QuerySet[ScanDiscovery],
        user: Any,
    ) -> tuple[int, int]:
        done = failed = 0
        for pk in queryset.values_list("pk", flat=True):
            if decide(pk, user)["status"] == "success":
                done += 1
            else:
                failed += 1
        return done, failed

    @action(description=_("Approve selected discoveries"), permissions=["change"])
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[ScanDiscovery]
    ) -> None:
        done, failed = self._count(scanner.approve_discovery, queryset, request.user)
        self.message_user(
            request,
            _("Approved %(done)d discoveries; %(failed)d were not pending.")
            % {"done": done, "failed": failed},
        )

    @action(description=_("Reject selected discoveries"), permissions=["change"])
    def reject_selected(
        self, request: HttpRequest, queryset: QuerySet[ScanDiscovery]
    ) -> None:
        done, failed = self._count(scanner.reject_discovery, queryset, request.user)
        self.message_user(
            request,
            _("Rejected %(done)d discoveries; %(failed)d were not pending.")
            % {"done": done, "failed": failed},
        )

    def _review(
        self,
        request: HttpRequest,
        object_id: str,
        decide: Callable[[int, Any], dict[str, Any]],
        success: str,
    ) -> HttpResponse:
        discovery = self.get_object(request, object_id)
        if discovery is None:
            raise Http404
        if decide(discovery.pk, request.user)["status"] == "success":
            self.log_change(request, discovery, success)
            self.message_user(request, success, messages.SUCCESS)
        else:
            self.message_user(
                request, _("This discovery was already reviewed."), messages.ERROR
            )
        return htmx_redirect(return_url(request, change_url(self, discovery.pk)))

    @action(
        description=_("Approve"),
        icon="check_circle",
        variant=ActionVariant.SUCCESS,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Approve this placement?"),
            _("An active ad placement is created from this discovery."),
            submit=_("Approve"),
        ),
    )
    def approve(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._review(
            request, object_id, scanner.approve_discovery, str(_("Placement approved."))
        )

    @action(
        description=_("Reject"),
        icon="cancel",
        permissions=["change"],
        dialog=confirm_dialog(_("Reject this placement?"), submit=_("Reject")),
    )
    def reject(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._review(
            request, object_id, scanner.reject_discovery, str(_("Placement rejected."))
        )


@admin.register(TemplateAdExclusion)
class TemplateAdExclusionAdmin(ModelAdmin):
    list_display = ("template_path", "reason", "excluded_by", "created_at")
    search_fields = ("template_path", "reason")
    readonly_fields = ("excluded_by",)

    def save_model(
        self,
        request: HttpRequest,
        obj: TemplateAdExclusion,
        form: ModelForm,
        change: bool,
    ) -> None:
        if not change and obj.excluded_by_id is None:
            obj.excluded_by_id = request.user.pk
        super().save_model(request, obj, form, change)


# --------------------------------------------------------------------------
# Affiliate catalogue
# --------------------------------------------------------------------------
@admin.register(AffiliateProvider)
class AffiliateProviderAdmin(AuditedModelAdmin):
    list_display = ("name", "provider_type", "is_enabled", "priority", "products_count")
    list_filter = (
        ("provider_type", ChoicesDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
    )
    search_fields = ("name", "associate_tag")
    readonly_fields = (
        "created_by",
        "updated_by",
        "last_sync_at",
        "sync_status",
        "products_count",
    )
    fieldsets = (
        (None, {"fields": ("name", "provider_type", "is_enabled", "priority")}),
        (
            _("API credentials"),
            {
                "classes": ("tab",),
                "fields": ("api_key", "api_secret", "associate_tag", "partner_id"),
            },
        ),
        (
            _("Configuration"),
            {
                "classes": ("tab",),
                "fields": (
                    "api_endpoint",
                    "api_region",
                    "marketplace",
                    "default_tracking_params",
                    "commission_rate",
                    "cookie_duration_days",
                ),
            },
        ),
        (
            _("Sync status"),
            {
                "classes": ("tab",),
                "fields": ("last_sync_at", "sync_status", "products_count"),
            },
        ),
        AUDIT_FIELDSET,
    )


@admin.register(AffiliateProductCategory)
class AffiliateProductCategoryAdmin(ModelAdmin):
    list_display = ("name", "slug", "parent", "is_active")
    list_filter = (
        ("is_active", BooleanRadioFilter),
        ("parent", RelatedDropdownFilter),
    )
    search_fields = ("name", "slug", "device_keywords")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    raw_id_fields = ("parent",)
    fieldsets = (
        (None, {"fields": ("name", "slug", "parent", "is_active")}),
        (_("Targeting"), {"fields": ("device_keywords", "brand_match", "icon")}),
    )


class AffiliateProductMatchInline(TabularInline):
    model = AffiliateProductMatch
    tab = True
    fields = (
        "brand",
        "model",
        "variant",
        "blog_post",
        "match_type",
        "relevance_score",
        "is_pinned",
        "is_hidden",
    )
    raw_id_fields = ("brand", "model", "variant", "blog_post")


@admin.register(AffiliateProduct)
class AffiliateProductAdmin(AuditedModelAdmin):
    list_display = (
        "name",
        "provider",
        "product_type",
        "display_price",
        "rating_stars",
        "is_enabled",
        "is_in_stock",
        "clicks",
        "conversions",
    )
    list_filter = (
        ("provider", RelatedDropdownFilter),
        ("product_type", ChoicesDropdownFilter),
        ("category", RelatedDropdownFilter),
        ("is_enabled", BooleanRadioFilter),
        ("is_in_stock", BooleanRadioFilter),
        ("is_universal", BooleanRadioFilter),
    )
    search_fields = ("name", "external_id", "asin", "target_keywords")
    readonly_fields = (
        "created_by",
        "updated_by",
        "price_updated_at",
        "impressions",
        "clicks",
        "conversions",
        "revenue",
    )
    raw_id_fields = ("provider", "category")
    inlines = (AffiliateProductMatchInline,)
    filter_horizontal = ("target_brands", "target_models")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "provider",
                    "category",
                    "name",
                    "slug",
                    "description",
                    "product_type",
                )
            },
        ),
        (
            _("Pricing & links"),
            {
                "classes": ("tab",),
                "fields": (
                    "price",
                    "sale_price",
                    "currency",
                    "price_updated_at",
                    "product_url",
                    "affiliate_url",
                    "external_id",
                    "asin",
                    "upc",
                    "ean",
                ),
            },
        ),
        (
            _("Images"),
            {
                "classes": ("tab",),
                "fields": ("image_url", "thumbnail_url", "additional_images"),
            },
        ),
        (
            _("Targeting & status"),
            {
                "classes": ("tab",),
                "fields": (
                    "target_brands",
                    "target_models",
                    "target_keywords",
                    "is_universal",
                    "rating",
                    "review_count",
                    "is_enabled",
                    "is_in_stock",
                    "availability_message",
                ),
            },
        ),
        (
            _("Performance"),
            {
                "classes": ("tab",),
                "fields": (
                    "impressions",
                    "clicks",
                    "conversions",
                    "revenue",
                    "ai_relevance_score",
                    "api_data",
                ),
            },
        ),
        AUDIT_FIELDSET,
    )

    @display(description=_("Price"), ordering="price")
    def display_price(self, obj: AffiliateProduct) -> str:
        if obj.sale_price and obj.sale_price < obj.price:
            return f"{obj.currency} {obj.sale_price} (was {obj.price})"
        return f"{obj.currency} {obj.get_display_price()}"

    @display(description=_("Rating"), ordering="rating")
    def rating_stars(self, obj: AffiliateProduct) -> str:
        filled = max(0, min(5, int(obj.rating)))
        return f"{'★' * filled}{'☆' * (5 - filled)} ({obj.review_count})"


@admin.register(AffiliateProductMatch)
class AffiliateProductMatchAdmin(ModelAdmin):
    list_display = (
        "product",
        "target_display",
        "match_type",
        "relevance_score",
        "is_pinned",
        "is_hidden",
    )
    list_filter = (
        ("match_type", ChoicesDropdownFilter),
        ("is_pinned", BooleanRadioFilter),
        ("is_hidden", BooleanRadioFilter),
    )
    search_fields = ("product__name", "brand__name", "model__name")
    raw_id_fields = ("product", "brand", "model", "variant", "blog_post")

    @display(description=_("Target"))
    def target_display(self, obj: AffiliateProductMatch) -> str:
        target = obj.brand or obj.model or obj.variant or obj.blog_post
        return str(target) if target else "—"


@admin.register(AffiliateClick)
class AffiliateClickAdmin(ReadOnlyModelAdmin):
    list_display = ("product", "provider", "user", "country", "created_at")
    list_filter = (
        ("provider", RelatedDropdownFilter),
        "country",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("product__name", "user__email", "page_url")
    readonly_fields = (
        "product",
        "provider",
        "user",
        "session_id",
        "ip_address",
        "user_agent",
        "page_url",
        "referrer",
        "brand_slug",
        "model_slug",
        "firmware_id",
        "blog_slug",
        "country",
        "region",
        "created_at",
    )
    date_hierarchy = "created_at"


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
@admin.register(AdsSettings)
class AdsSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "ads_enabled",
        "auto_ads_enabled",
        "rewarded_ads_enabled",
        "affiliate_products_enabled",
        "ad_aggressiveness_level",
    )
    fieldsets = (
        (
            _("Master controls"),
            {
                "fields": (
                    "ads_enabled",
                    "affiliate_enabled",
                    "ad_networks_enabled",
                    "ad_aggressiveness_level",
                ),
            },
        ),
        (
            _("Auto ads"),
            {
                "classes": ("tab",),
                "fields": (
                    "auto_ads_enabled",
                    "auto_ads_scan_interval",
                    "auto_ads_in_article",
                    "auto_ads_in_feed",
                    "auto_ads_anchor",
                    "auto_ads_vignette",
                    "auto_ads_sidebar",
                ),
            },
        ),
        (
            _("Rewarded ads"),
            {
                "classes": ("tab",),
                "fields": ("rewarded_ads_enabled",),
                "description": _(
                    "Master switch only. What each rewarded ad gives, its limits and "
                    "the watch time are set per Rewarded Ad Config; credits are set in "
                    "Credits economy → Reward rules."
                ),
            },
        ),
        (
            _("Affiliate products"),
            {
                "classes": ("tab",),
                "fields": (
                    "affiliate_products_enabled",
                    "affiliate_products_max_per_page",
                    "affiliate_products_cache_hours",
                    "affiliate_products_auto_fetch",
                    "affiliate_products_show_on_firmware",
                    "affiliate_products_show_on_model",
                    "affiliate_products_show_on_brand",
                    "affiliate_products_show_on_blog",
                    "affiliate_products_fallback_keywords",
                    "amazon_paapi_enabled",
                    "amazon_paapi_access_key",
                    "amazon_paapi_secret_key",
                    "amazon_paapi_partner_tag",
                    "amazon_paapi_region",
                    "amazon_paapi_marketplace",
                ),
            },
        ),
        (
            _("Networks"),
            {
                "classes": ("tab",),
                "fields": (
                    "adsense_enabled",
                    "adsense_publisher_id",
                    "adsense_auto_ads",
                    "medianet_enabled",
                    "medianet_customer_id",
                    "ezoic_enabled",
                    "ezoic_site_id",
                    "adthrive_enabled",
                    "adthrive_site_id",
                    "mediavine_enabled",
                    "mediavine_site_id",
                    "propellerads_enabled",
                    "propellerads_zone_id",
                    "amazon_ads_enabled",
                    "amazon_ads_publisher_id",
                    "infolinks_enabled",
                    "infolinks_pid",
                    "carbon_enabled",
                    "carbon_serve_id",
                    "buysellads_enabled",
                    "buysellads_zone_id",
                ),
            },
        ),
        (
            _("Optimisation"),
            {
                "classes": ("tab",),
                "fields": (
                    "header_bidding_enabled",
                    "prebid_timeout_ms",
                    "prebid_bidders",
                    "native_ads_enabled",
                    "native_ads_style",
                    "ai_optimization_enabled",
                    "ai_model_provider",
                    "ai_optimize_placements",
                    "ai_optimize_creatives",
                    "ai_optimize_bidding",
                ),
            },
        ),
        (
            _("Privacy & limits"),
            {
                "classes": ("tab",),
                "fields": (
                    "max_ads_per_page",
                    "min_content_between_ads",
                    "refresh_interval_seconds",
                    "lazy_load_ads",
                    "excluded_pages",
                    "excluded_categories",
                    "excluded_user_roles",
                    "custom_config",
                ),
            },
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
