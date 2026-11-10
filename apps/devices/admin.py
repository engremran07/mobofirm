"""Device identity admin: browser registrations, quotas, policies and audit events."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from urllib.parse import unquote

from django.contrib import admin, messages
from django.db.models import Q, QuerySet
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
from unfold.forms import BaseDialogForm

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
from apps.devices import slots
from apps.devices.dialogs import DeclineSlotRequestForm, ResetSlotsForm
from apps.devices.models import (
    AppPolicy,
    BehaviorInsight,
    Device,
    DeviceBrowserLink,
    DeviceConfig,
    DeviceEvent,
    DeviceFingerprint,
    DeviceResetAllowance,
    DeviceSlotRequest,
    DeviceSlotReset,
    RegistryEvent,
    TrustScore,
)
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
        "identity",
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
        "os_credential_id",
        "os_credential_id_hash",
        "os_credential_public_key",
        "os_credential_alg",
        "os_credential_sign_count",
        "os_credential_rp_id",
        "first_seen_at",
        "last_seen_at",
        "revoked_at",
    )
    list_select_related = ("user",)
    inlines = (DeviceEventInline,)
    actions = ("mark_trusted", "block_devices", "unblock_devices", "revoke_devices")
    actions_row = ("trust", "block", "unblock", "revoke", "reset_slots")
    actions_detail = ("trust", "block", "unblock", "revoke", "reset_slots")

    @display(description=_("Identity"))
    def identity(self, obj: Device) -> str:
        if obj.is_os_identity:
            return str(_("This computer (OS)"))
        if obj.browser_key_hash:
            return str(_("This browser only"))
        return str(_("Legacy"))

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
            Q(browser_key_hash__gt="") | Q(os_credential_id_hash__gt=""),
            is_blocked=False,
            revoked_at__isnull=True,
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
        # Revoking disables the OS credential: its browsers stop being recognised
        # on it and must register (or verify another credential) again.
        DeviceBrowserLink.objects.filter(
            device_id__in=[device.pk for device in devices]
        ).delete()
        updated = active.update(is_trusted=False, revoked_at=timezone.now())
        self._audit(request, devices, "policy_violation", "revoked_by_admin")
        return f"{updated} device registration(s) revoked.", messages.SUCCESS

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
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
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
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._block)

    @action(
        description=_("Unblock"),
        icon="lock_open",
        permissions=["change"],
        dialog=confirm_dialog(_("Unblock this device?"), submit=_("Unblock")),
    )
    def unblock(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._unblock)

    @action(
        description=_("Revoke registration"),
        icon="key_off",
        variant=ActionVariant.WARNING,
        permissions=["change"],
        dialog=confirm_dialog(
            _("Revoke this device registration?"),
            _(
                "The registration and its computer credential stop working; the "
                "browser or computer must register again before it is trusted."
            ),
            submit=_("Revoke"),
        ),
    )
    def revoke(
        self, request: HttpRequest, _form: ConfirmForm, object_id: str
    ) -> HttpResponse:
        return self._single(request, object_id, self._revoke)

    def has_reset_device_slots_permission(
        self, request: HttpRequest, _object_id: str | None = None
    ) -> bool:
        return bool(request.user.has_perm(slots.STAFF_PERMISSION))

    @action(
        description=_("Reset account slots"),
        icon="restart_alt",
        variant=ActionVariant.WARNING,
        permissions=["reset_device_slots"],
        url_path="reset-account-slots",
        dialog=confirm_dialog(
            _("Reset this account's browser slots?"),
            _(
                "Every approved browser of the account goes back to “awaiting "
                "approval” and can be approved again within its limit. Nothing is "
                "deleted; the reset is recorded with your name and reason."
            ),
            ResetSlotsForm,
            submit=_("Reset slots"),
        ),
    )
    def reset_slots(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        device = self.get_object(request, unquote(object_id))
        if device is None:
            raise Http404
        try:
            reset = slots.reset_slots_as_staff(
                device.user, actor=request.user, reason=str(form.cleaned_data["reason"])
            )
        except slots.SlotResetError as exc:
            self.message_user(request, exc.message, messages.ERROR)
        else:
            self.log_change(request, device, f"Slot reset #{reset.pk}")
            self.message_user(
                request,
                _("%(count)d approved browser(s) released.")
                % {"count": reset.devices_released},
            )
        return htmx_redirect(return_url(request, change_url(self, device.pk)))


@admin.register(DeviceConfig)
class DeviceConfigAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            _("Browser registration"),
            {
                "fields": (
                    "slot_reset_monthly_cap",
                    "monthly_device_quota",
                    "yearly_device_quota",
                    "device_expiry_days",
                ),
                "description": _(
                    "How many browsers an account may approve is part of its download "
                    "package (Shop → Packages → Basic info → Limit devices), including "
                    "staff overrides and campaign bonuses. Quotas here limit new "
                    "registrations per rolling 30 days / 365 days. The price of a paid "
                    "slot reset is under Credits economy → Credit prices (“Reset "
                    "registered-browser slots”); a package can override it "
                    "(Shop → Packages → Basic info). Old "
                    "machine-fingerprint records remain historical."
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


BADGE_SLOT_METHOD: dict[str, str] = {
    DeviceSlotReset.Method.STAFF: "info",
    DeviceSlotReset.Method.CREDITS: "success",
    DeviceSlotReset.Method.REWARDED_AD: "warning",
}
BADGE_SLOT_REQUEST: dict[str, str] = {
    DeviceSlotRequest.Status.OPEN: "warning",
    DeviceSlotRequest.Status.RESOLVED: "success",
    DeviceSlotRequest.Status.DECLINED: "danger",
}


@admin.register(DeviceSlotReset)
class DeviceSlotResetAdmin(ReadOnlyModelAdmin):
    """Permanent record of every slot reset (events are purged after retention)."""

    list_display = (
        "created_at",
        "user",
        "method_badge",
        "devices_released",
        "credits_spent",
        "actor",
        "reason",
    )
    list_filter = (
        ("method", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "key", "reason")
    raw_id_fields = ("user", "actor")
    date_hierarchy = "created_at"
    list_select_related = ("user", "actor")

    @display(description=_("Method"), ordering="method", label=BADGE_SLOT_METHOD)
    def method_badge(self, obj: DeviceSlotReset) -> tuple[str, str]:
        return obj.method, obj.get_method_display()


@admin.register(DeviceSlotRequest)
class DeviceSlotRequestAdmin(ReadOnlyModelAdmin):
    """Users at their device limit who asked staff to free a slot."""

    list_display = (
        "created_at",
        "user",
        "status_badge",
        "message",
        "resolved_by",
        "resolved_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "message")
    raw_id_fields = ("user", "resolved_by", "reset")
    list_select_related = ("user", "resolved_by")
    actions_row = ("reset_and_close", "decline")
    actions_detail = ("reset_and_close", "decline")

    @display(description=_("Status"), ordering="status", label=BADGE_SLOT_REQUEST)
    def status_badge(self, obj: DeviceSlotRequest) -> tuple[str, str]:
        return obj.status, obj.get_status_display()

    def has_reset_device_slots_permission(
        self, request: HttpRequest, _object_id: str | None = None
    ) -> bool:
        return bool(request.user.has_perm(slots.STAFF_PERMISSION))

    def _answer(
        self, request: HttpRequest, object_id: str, form: BaseDialogForm, *, reset: bool
    ) -> HttpResponse:
        slot_request = self.get_object(request, unquote(object_id))
        if slot_request is None:
            raise Http404
        try:
            slots.resolve_slot_request(
                slot_request,
                actor=request.user,
                reset=reset,
                note=str(form.cleaned_data["reason"]),
            )
        except slots.SlotResetError as exc:
            self.message_user(request, exc.message, messages.ERROR)
        else:
            self.message_user(request, _("Request answered."))
        return htmx_redirect(return_url(request, change_url(self, slot_request.pk)))

    @action(
        description=_("Reset slots"),
        icon="restart_alt",
        variant=ActionVariant.SUCCESS,
        permissions=["reset_device_slots"],
        url_path="reset-and-close",
        dialog=confirm_dialog(
            _("Reset this account's browser slots?"),
            _(
                "Releases every approved browser of the user, closes the request and "
                "notifies them. Recorded with your name and the note below."
            ),
            ResetSlotsForm,
            submit=_("Reset slots"),
        ),
    )
    def reset_and_close(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._answer(request, object_id, form, reset=True)

    @action(
        description=_("Decline"),
        icon="block",
        variant=ActionVariant.DANGER,
        permissions=["reset_device_slots"],
        url_path="decline-request",
        dialog=confirm_dialog(
            _("Decline this request?"),
            _("The user is notified with your answer."),
            DeclineSlotRequestForm,
            submit=_("Decline"),
        ),
    )
    def decline(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._answer(request, object_id, form, reset=False)


@admin.register(DeviceResetAllowance)
class DeviceResetAllowanceAdmin(ReadOnlyModelAdmin):
    """Resets earned by completing a rewarded ad, spent from the user's browsers page."""

    list_display = ("created_at", "user", "source", "expires_at", "consumed_at")
    list_filter = (
        "source",
        ("consumed_at", RangeDateTimeFilter),
        ("expires_at", RangeDateTimeFilter),
    )
    search_fields = ("user__email", "user__username", "key")
    raw_id_fields = ("user",)
    list_select_related = ("user",)


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
