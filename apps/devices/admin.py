"""Device identity admin: browser registrations, quotas, policies and audit events."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
)
from unfold.enums import ActionVariant

from apps.core.admin_base import (
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
from apps.devices.models import (
    AppPolicy,
    BehaviorInsight,
    Device,
    DeviceConfig,
    DeviceEvent,
    DeviceFingerprint,
    QuotaTier,
    RegistryEvent,
    TrustScore,
)
from apps.devices.models_quota import UserDeviceQuota
from apps.devices.services import mark_device_trusted

BADGE_DEVICE_STATE: dict[str, str] = {
    "trusted": "success",
    "blocked": "danger",
    "revoked": "warning",
    "pending": "info",
}
BADGE_TRUST_LEVEL: dict[str, str] = {
    DeviceFingerprint.TrustLevel.UNKNOWN: "info",
    DeviceFingerprint.TrustLevel.LOW: "danger",
    DeviceFingerprint.TrustLevel.MEDIUM: "warning",
    DeviceFingerprint.TrustLevel.HIGH: "success",
    DeviceFingerprint.TrustLevel.TRUSTED: "success",
}
BADGE_SEVERITY: dict[str, str] = {
    BehaviorInsight.Severity.LOW: "info",
    BehaviorInsight.Severity.MEDIUM: "warning",
    BehaviorInsight.Severity.HIGH: "danger",
    BehaviorInsight.Severity.CRITICAL: "danger",
}
BADGE_INSIGHT_STATUS: dict[str, str] = {
    BehaviorInsight.Status.OPEN: "warning",
    BehaviorInsight.Status.APPROVED: "success",
    BehaviorInsight.Status.REJECTED: "danger",
    BehaviorInsight.Status.RESOLVED: "success",
}
BADGE_SUCCESS: dict[str, str] = {"ok": "success", "failed": "danger"}


def _is_superuser(request: HttpRequest) -> bool:
    return bool(getattr(request.user, "is_superuser", False))


class DeviceEventInline(TabularInline):
    """Latest audit events for the device, read-only."""

    model = DeviceEvent
    tab = True
    fields = ("created_at", "event_type", "success", "reason", "ip")
    readonly_fields = ("created_at", "event_type", "success", "reason", "ip")
    can_delete = False
    ordering = ("-created_at",)
    verbose_name_plural = _("Events")

    def has_add_permission(
        self, request: HttpRequest, obj: Device | None = None
    ) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: Device | None = None
    ) -> bool:
        return False


@admin.register(Device)
class DeviceAdmin(ModelAdmin):
    list_display = (
        "user",
        "display_name",
        "os_family",
        "browser_family",
        "state",
        "risk_score",
        "last_seen_at",
    )
    list_filter = (
        ("is_trusted", BooleanRadioFilter),
        ("is_blocked", BooleanRadioFilter),
        ("revoked_at", RangeDateTimeFilter),
        ("risk_score", RangeNumericFilter),
        ("last_seen_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "display_name")
    raw_id_fields = ("user",)
    readonly_fields = (
        "browser_key_hash",
        "first_seen_at",
        "last_seen_at",
        "revoked_at",
    )
    list_select_related = ("user",)
    inlines = (DeviceEventInline,)
    actions = ("mark_trusted", "block_devices", "unblock_devices", "revoke_devices")
    actions_row = ("trust", "block", "unblock", "revoke")
    actions_detail = ("trust", "block", "unblock", "revoke")

    @display(description=_("State"), label=BADGE_DEVICE_STATE)
    def state(self, obj: Device) -> tuple[str, str]:
        if obj.is_blocked:
            return "blocked", str(_("Blocked"))
        if obj.revoked_at:
            return "revoked", str(_("Revoked"))
        if obj.is_trusted:
            return "trusted", str(_("Trusted"))
        return "pending", str(_("Registered"))

    def _audit(
        self,
        request: HttpRequest,
        devices: Iterable[Device],
        event_type: str,
        reason: str,
    ) -> None:
        DeviceEvent.objects.bulk_create(
            [
                DeviceEvent(
                    device=device,
                    user=device.user,
                    event_type=event_type,
                    success=True,
                    reason=reason,
                    metadata={"admin_user_id": request.user.pk},
                )
                for device in devices
            ]
        )

    def _trust(
        self, _request: HttpRequest, queryset: QuerySet[Device]
    ) -> tuple[str, int]:
        trusted = 0
        eligible = queryset.filter(
            browser_key_hash__gt="", is_blocked=False, revoked_at__isnull=True
        ).select_related("user")
        for device in eligible:
            if mark_device_trusted(device.pk, device.user_id):
                trusted += 1
        skipped = queryset.count() - trusted
        message = (
            f"{trusted} device(s) marked as trusted; {skipped} skipped "
            "(blocked, revoked, unregistered or over the device limit)."
        )
        return message, messages.SUCCESS if trusted else messages.WARNING

    def _block(
        self, request: HttpRequest, queryset: QuerySet[Device]
    ) -> tuple[str, int]:
        devices = list(queryset.select_related("user"))
        updated = queryset.update(
            is_blocked=True, is_trusted=False, max_privilege_level="blocked"
        )
        self._audit(request, devices, "device_blocked", "blocked_by_admin")
        return f"{updated} device(s) blocked.", messages.SUCCESS

    def _unblock(
        self, request: HttpRequest, queryset: QuerySet[Device]
    ) -> tuple[str, int]:
        devices = list(queryset.select_related("user"))
        updated = queryset.update(is_blocked=False, max_privilege_level="normal")
        self._audit(request, devices, "policy_violation", "unblocked_by_admin")
        return f"{updated} device(s) unblocked.", messages.SUCCESS

    def _revoke(
        self, request: HttpRequest, queryset: QuerySet[Device]
    ) -> tuple[str, int]:
        active = queryset.filter(revoked_at__isnull=True)
        devices = list(active.select_related("user"))
        updated = active.update(is_trusted=False, revoked_at=timezone.now())
        self._audit(request, devices, "policy_violation", "revoked_by_admin")
        return f"{updated} browser registration(s) revoked.", messages.SUCCESS

    # -- bulk actions ---------------------------------------------------------
    @action(description=_("Mark selected devices as trusted"), permissions=["change"])
    def mark_trusted(self, request: HttpRequest, queryset: QuerySet[Device]) -> None:
        message, level = self._trust(request, queryset)
        self.message_user(request, message, level)

    @action(description=_("Block selected devices"), permissions=["change"])
    def block_devices(self, request: HttpRequest, queryset: QuerySet[Device]) -> None:
        message, level = self._block(request, queryset)
        self.message_user(request, message, level)

    @action(description=_("Unblock selected devices"), permissions=["change"])
    def unblock_devices(self, request: HttpRequest, queryset: QuerySet[Device]) -> None:
        message, level = self._unblock(request, queryset)
        self.message_user(request, message, level)

    @action(
        description=_("Revoke selected browser registrations"), permissions=["change"]
    )
    def revoke_devices(self, request: HttpRequest, queryset: QuerySet[Device]) -> None:
        message, level = self._revoke(request, queryset)
        self.message_user(request, message, level)

    # -- row / detail actions: each link opens a confirmation dialog -------------------
    def _single(
        self,
        request: HttpRequest,
        object_id: str,
        step: Callable[[HttpRequest, QuerySet[Device]], tuple[str, int]],
    ) -> HttpResponse:
        device = self.get_object(request, object_id)
        if device is None:
            raise Http404
        message, level = step(request, Device.objects.filter(pk=device.pk))
        if level == messages.SUCCESS:
            self.log_change(request, device, message)
        self.message_user(request, message, level)
        return htmx_redirect(return_url(request, change_url(self, device.pk)))

    @action(
        description=_("Trust"),
        icon="verified_user",
        variant=ActionVariant.SUCCESS,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Trust this browser?"),
            _("It counts against the account's device allowance."),
            submit=_("Trust"),
        ),
    )
    def trust(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._trust)

    @action(
        description=_("Block"),
        icon="block",
        variant=ActionVariant.DANGER,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Block this device?"),
            _("A blocked device loses trusted status and cannot sign in."),
            submit=_("Block"),
        ),
    )
    def block(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._block)

    @action(
        description=_("Unblock"),
        icon="lock_open",
        permissions=["change"],
        dialog=confirm_dialog(_("Unblock this device?"), submit=_("Unblock")),
    )
    def unblock(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._unblock)

    @action(
        description=_("Revoke registration"),
        icon="key_off",
        variant=ActionVariant.WARNING,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Revoke this browser registration?"),
            _("The browser must register again before it is trusted."),
            submit=_("Revoke"),
        ),
    )
    def revoke(
        self, request: HttpRequest, form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._revoke)


@admin.register(DeviceConfig)
class DeviceConfigAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            _("Browser registration"),
            {
                "fields": (
                    "max_devices_default",
                    "monthly_device_quota",
                    "yearly_device_quota",
                    "device_expiry_days",
                ),
                "description": _(
                    "Only approved first-party browser profiles count against this "
                    "allowance. Quotas limit new registrations per rolling 30 days / "
                    "365 days. Old machine-fingerprint records remain historical."
                ),
            },
        ),
        (
            _("Additional verification"),
            {"fields": ("strict_new_device_login", "require_mfa_on_new_device")},
        ),
        (
            _("Risk analysis"),
            {"fields": ("ai_risk_scoring_enabled", "risk_mfa_threshold")},
        ),
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: DeviceConfig | None = None
    ) -> bool:
        return _is_superuser(request)


@admin.register(AppPolicy)
class AppPolicyAdmin(ModelAdmin):
    list_display = (
        "name",
        "device_locking_mode",
        "mfa_requirement",
        "enhanced_fingerprinting",
        "ai_risk_scoring",
        "monthly_device_quota",
        "yearly_device_quota",
        "ad_unlock_enabled",
    )
    search_fields = ("name",)

    def has_change_permission(
        self, request: HttpRequest, obj: AppPolicy | None = None
    ) -> bool:
        return _is_superuser(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: AppPolicy | None = None
    ) -> bool:
        return _is_superuser(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return _is_superuser(request)


@admin.register(DeviceEvent)
class DeviceEventAdmin(NoAddModelAdmin):
    list_display = (
        "created_at",
        "event_type",
        "user",
        "device",
        "outcome",
        "reason",
        "ip",
    )
    list_filter = (
        ("event_type", ChoicesDropdownFilter),
        ("success", BooleanRadioFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "user__email",
        "user__username",
        "device__os_fingerprint",
        "reason",
        "ip",
        "user_agent",
    )
    raw_id_fields = ("user", "device")
    readonly_fields = ("created_at",)
    date_hierarchy = "created_at"

    @display(description=_("Outcome"), ordering="success", label=BADGE_SUCCESS)
    def outcome(self, obj: DeviceEvent) -> tuple[str, str]:
        if obj.success:
            return "ok", str(_("Success"))
        return "failed", str(_("Failed"))


@admin.register(UserDeviceQuota)
class UserDeviceQuotaAdmin(ModelAdmin):
    list_display = ("user", "max_devices", "window", "last_reset_at")
    search_fields = ("user__email", "user__username")
    list_filter = (
        ("window", ChoicesDropdownFilter),
        ("last_reset_at", RangeDateTimeFilter),
    )
    raw_id_fields = ("user",)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return _is_superuser(request)

    def has_change_permission(
        self, request: HttpRequest, obj: UserDeviceQuota | None = None
    ) -> bool:
        return _is_superuser(request)

    def has_delete_permission(
        self, request: HttpRequest, obj: UserDeviceQuota | None = None
    ) -> bool:
        return _is_superuser(request)


@admin.register(DeviceFingerprint)
class DeviceFingerprintAdmin(ReadOnlyModelAdmin):
    list_display = (
        "fingerprint_hash",
        "trust_badge",
        "browser",
        "os",
        "device_type",
        "is_bot",
        "visit_count",
        "last_seen",
    )
    list_filter = (
        ("trust_level", ChoicesDropdownFilter),
        ("is_bot", BooleanRadioFilter),
        "device_type",
    )
    search_fields = ("fingerprint_hash", "browser", "os", "ip")
    readonly_fields = (
        "fingerprint_hash",
        "signals",
        "browser",
        "os",
        "device_type",
        "ip",
        "country",
        "trust_level",
        "is_bot",
        "user",
        "first_seen",
        "last_seen",
        "visit_count",
    )

    @display(description=_("Trust"), ordering="trust_level", label=BADGE_TRUST_LEVEL)
    def trust_badge(self, obj: DeviceFingerprint) -> tuple[str, str]:
        return obj.trust_level, obj.get_trust_level_display()


@admin.register(TrustScore)
class TrustScoreAdmin(ModelAdmin):
    list_display = (
        "fingerprint",
        "score",
        "signals_passed",
        "signals_failed",
        "last_updated",
    )
    raw_id_fields = ("fingerprint",)
    readonly_fields = ("last_updated",)


@admin.register(QuotaTier)
class QuotaTierAdmin(ModelAdmin):
    list_display = (
        "name",
        "min_trust_score",
        "max_trust_score",
        "daily_download_limit",
        "hourly_download_limit",
        "requires_ad",
        "can_bypass_captcha",
    )


@admin.register(RegistryEvent)
class RegistryEventAdmin(ModelAdmin):
    list_display = ("fingerprint", "action", "user", "ip", "created_at")
    list_filter = (
        ("action", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("fingerprint__fingerprint_hash", "ip")
    raw_id_fields = ("fingerprint", "user")
    readonly_fields = ("created_at",)


@admin.register(BehaviorInsight)
class BehaviorInsightAdmin(ModelAdmin):
    list_display = (
        "fingerprint",
        "severity_badge",
        "status_badge",
        "related_user",
        "created_at",
    )
    list_filter = (
        ("severity", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
    )
    search_fields = ("fingerprint__fingerprint_hash", "recommendation")
    raw_id_fields = ("fingerprint", "related_user")
    readonly_fields = ("created_at", "updated_at")
    actions = ("mark_resolved",)

    @display(description=_("Severity"), ordering="severity", label=BADGE_SEVERITY)
    def severity_badge(self, obj: BehaviorInsight) -> tuple[str, str]:
        return obj.severity, obj.get_severity_display()

    @display(description=_("Status"), ordering="status", label=BADGE_INSIGHT_STATUS)
    def status_badge(self, obj: BehaviorInsight) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    @action(description=_("Mark selected insights as resolved"), permissions=["change"])
    def mark_resolved(
        self, request: HttpRequest, queryset: QuerySet[BehaviorInsight]
    ) -> None:
        updated = queryset.exclude(status=BehaviorInsight.Status.RESOLVED).update(
            status=BehaviorInsight.Status.RESOLVED, updated_at=timezone.now()
        )
        self.message_user(
            request,
            _("%(count)d insight(s) resolved.") % {"count": updated},
            messages.SUCCESS if updated else messages.WARNING,
        )
