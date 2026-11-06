"""Firmware storage location administration (Unfold)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ModelAdmin,
    action,
    change_url,
    changelist_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

from .models import FirmwareStorageLocation
from .services import sync_all_firmware, verify_location

STATUS_BADGES = {
    FirmwareStorageLocation.Status.AVAILABLE.value: "success",
    FirmwareStorageLocation.Status.PENDING.value: "info",
    FirmwareStorageLocation.Status.MISSING.value: "danger",
    FirmwareStorageLocation.Status.MISMATCH.value: "danger",
    FirmwareStorageLocation.Status.DISABLED.value: "warning",
}


@register(FirmwareStorageLocation)
class FirmwareStorageLocationAdmin(ModelAdmin):
    list_display = (
        "object_id",
        "content_type",
        "provider",
        "status_badge",
        "size_bytes",
        "last_verified_at",
    )
    list_filter = (
        ("provider", ChoicesDropdownFilter),
        ("status", ChoicesDropdownFilter),
        ("content_type", RelatedDropdownFilter),
        ("last_verified_at", RangeDateTimeFilter),
    )
    search_fields = ("object_id", "relative_path", "external_url", "sha256")
    list_select_related = ("content_type",)
    readonly_fields = (
        "sha256",
        "size_bytes",
        "status",
        "last_verified_at",
        "last_error",
        "created_at",
        "updated_at",
    )
    actions = ("verify_selected",)
    actions_row = ("verify_now",)
    actions_detail = ("verify_now",)
    actions_list = ("open_status", "sync_catalogue")
    fieldsets = (
        (
            _("Location"),
            {
                "fields": (
                    "content_type",
                    "object_id",
                    "provider",
                    "relative_path",
                    "external_url",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Integrity"),
            {
                "fields": (
                    "status",
                    "sha256",
                    "size_bytes",
                    "last_verified_at",
                    "last_error",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Metadata"),
            {"fields": ("created_at", "updated_at"), "classes": ["tab"]},
        ),
    )

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: FirmwareStorageLocation) -> str:
        return obj.status

    @action(description="Verify selected locations", permissions=["change"])
    def verify_selected(
        self, request: HttpRequest, queryset: QuerySet[FirmwareStorageLocation]
    ) -> None:
        results = [verify_location(location) for location in queryset]
        self.message_user(
            request, f"Verified {len(results)} location(s).", level=messages.SUCCESS
        )

    @action(
        description=_("Verify now"),
        icon="fact_check",
        permissions=["change"],
        url_path="verify-now",
        dialog=confirm_dialog(
            _("Verify this location"),
            _("Re-checks the stored bytes or link policy and records the result."),
            submit=_("Verify"),
        ),
    )
    def verify_now(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        location = self.get_object(request, unquote(object_id))
        if location is None:
            raise Http404
        checked = verify_location(location)
        self.message_user(
            request,
            _("Location is now “%(status)s”.")
            % {"status": checked.get_status_display()},
            level=(
                messages.SUCCESS
                if checked.status == FirmwareStorageLocation.Status.AVAILABLE
                else messages.WARNING
            ),
        )
        return htmx_redirect(return_url(request, change_url(self, location.pk)))

    @action(
        description=_("Synchronise catalogue"),
        icon="sync",
        permissions=["change"],
        url_path="sync-catalogue",
        dialog=confirm_dialog(
            _("Synchronise storage locations"),
            _(
                "Creates or updates a location row for every firmware record. "
                "Files are not verified until you run a verification."
            ),
            submit=_("Synchronise"),
        ),
    )
    def sync_catalogue(
        self, request: HttpRequest, form: BaseDialogForm
    ) -> HttpResponse:
        report = sync_all_firmware()
        self.message_user(
            request,
            _(
                "Synchronised %(synced)d location(s) from %(seen)d firmware "
                "record(s); %(unmanaged)d not managed."
            )
            % {
                "synced": report.locations_synced,
                "seen": report.firmware_seen,
                "unmanaged": report.unmanaged,
            },
        )
        return htmx_redirect(return_url(request, changelist_url(self)))

    @action(
        description=_("Health overview"),
        icon="monitor_heart",
        permissions=["view"],
        url_path="open-status",
    )
    def open_status(self, request: HttpRequest) -> HttpResponse:
        return redirect("storage:status")
