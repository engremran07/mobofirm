from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_GET, require_POST

from apps.core.decorators import require_app_enabled
from apps.firmwares.catalog import TYPE_KEY_BY_CLASS

from . import providers
from .models import FirmwareStorageLocation
from .providers import provider_statuses
from .services import status_overview, verify_location

PAGE_SIZE = 25
VERIFY_BATCH = 25


def _location_row(location: FirmwareStorageLocation) -> dict[str, Any]:
    """A location with links to its firmware's public page and admin form."""
    firmware = location.firmware
    public_url = admin_url = ""
    label = f"Firmware {location.object_id[:8]} (record missing)"
    if firmware is not None:
        label = (
            firmware.model.display_name if firmware.model else str(firmware.brand or "")
        ) or firmware.original_file_name
        type_key = TYPE_KEY_BY_CLASS.get(type(firmware), "")
        meta = type(firmware)._meta
        try:
            if type_key:
                public_url = reverse(
                    "firmwares:firmware_detail",
                    kwargs={"firmware_type": type_key, "firmware_id": str(firmware.pk)},
                )
            admin_url = reverse(
                f"admin:{meta.app_label}_{meta.model_name}_change", args=[firmware.pk]
            )
        except NoReverseMatch:
            pass
    return {
        "location": location,
        "label": label,
        "file_name": firmware.original_file_name if firmware is not None else "",
        "type_label": location.content_type.name.title(),
        "public_url": public_url,
        "admin_url": admin_url,
    }


@require_GET
@require_app_enabled("storage")
@staff_member_required(login_url="admin:login")
def status(request: HttpRequest) -> HttpResponse:
    """Staff view of provider availability, capacity and location health."""
    status_filter = request.GET.get("status", "")
    provider_filter = request.GET.get("provider", "")
    query = request.GET.get("q", "").strip()[:64]

    locations = FirmwareStorageLocation.objects.select_related("content_type")
    if status_filter in FirmwareStorageLocation.Status.values:
        locations = locations.filter(status=status_filter)
    else:
        status_filter = ""
    if provider_filter in {value for value, _ in providers.PROVIDER_CHOICES}:
        locations = locations.filter(provider=provider_filter)
    else:
        provider_filter = ""
    if query:
        locations = locations.filter(object_id__icontains=query)

    page = Paginator(locations.order_by("status", "-updated_at"), PAGE_SIZE).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "storage/status.html",
        {
            "providers": provider_statuses(),
            "overview": status_overview(),
            "page_obj": page,
            "rows": [_location_row(location) for location in page.object_list],
            "statuses": FirmwareStorageLocation.Status.choices,
            "provider_choices": providers.PROVIDER_CHOICES,
            "status_filter": status_filter,
            "provider_filter": provider_filter,
            "query": query,
            "pending_count": FirmwareStorageLocation.objects.filter(
                status=FirmwareStorageLocation.Status.PENDING
            ).count(),
            "verify_batch": VERIFY_BATCH,
        },
    )


@require_POST
@require_app_enabled("storage")
@staff_member_required(login_url="admin:login")
def verify(request: HttpRequest, location_id) -> HttpResponse:
    location = get_object_or_404(FirmwareStorageLocation, pk=location_id)
    verified = verify_location(location)
    messages.info(request, f"Verification result: {verified.get_status_display()}.")
    return redirect("storage:status")


@require_POST
@require_app_enabled("storage")
@staff_member_required(login_url="admin:login")
def verify_pending(request: HttpRequest) -> HttpResponse:
    """Verify the oldest pending locations (bounded so the request stays short)."""
    pending = FirmwareStorageLocation.objects.filter(
        status=FirmwareStorageLocation.Status.PENDING
    ).order_by("updated_at")[:VERIFY_BATCH]
    results = [verify_location(location) for location in pending]
    messages.info(request, f"Verified {len(results)} pending location(s).")
    return redirect("storage:status")
