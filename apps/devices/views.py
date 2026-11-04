from __future__ import annotations

import json
from typing import TYPE_CHECKING

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.devices.models import Device, DeviceConfig, DeviceEvent
from apps.devices.models_quota import UserDeviceQuota
from apps.devices.utils.device_fingerprint import make_os_fingerprint

if TYPE_CHECKING:
    from apps.firmwares.models import GSMArenaDevice


@staff_member_required(login_url="admin:login")
def admin_placeholder(request: HttpRequest) -> HttpResponse:
    return HttpResponse("Devices admin placeholder", content_type="text/plain")


@require_GET
def device_browse(request: HttpRequest) -> HttpResponse:
    """Public device catalog — multi-brand firmware library hub.

    Serves as the main landing page for the firmware library, showing:
    - Aggregate stats (brands, models, firmwares)
    - Featured brands
    - Full brand grid with model counts
    - Universal search across all brands/models/codenames
    """
    from apps.firmwares.models import Brand, GSMArenaDevice, Model, OfficialFirmware

    search_query = request.GET.get("q", "").strip()
    brands = Brand.objects.annotate(model_count=Count("models")).order_by("name")

    # Featured brands first, then alphabetical
    featured_brands = brands.filter(is_featured=True)

    if search_query:
        brands = brands.filter(name__icontains=search_query)

    # Aggregate stats for the whole platform
    total_models = Model.objects.filter(is_active=True).count()
    total_firmwares = OfficialFirmware.objects.filter(is_active=True).count()
    total_specs = GSMArenaDevice.objects.filter(review_status="approved").count()

    context = {
        "brands": brands,
        "featured_brands": featured_brands,
        "search_query": search_query,
        "total_brands": Brand.objects.count(),
        "total_models": total_models,
        "total_firmwares": total_firmwares,
        "total_specs": total_specs,
    }

    if request.headers.get("HX-Request"):
        return render(request, "devices/fragments/brand_list.html", context)
    return render(request, "devices/browse.html", context)


@require_GET
def device_search(request: HttpRequest) -> HttpResponse:
    """Universal search across all brands, models, codenames, model codes.

    Returns model results matching the query, grouped by brand.
    Works as HTMX fragment or standalone page.
    """
    from apps.firmwares.models import Model

    query = request.GET.get("q", "").strip()
    results = []
    if len(query) >= 2:  # noqa: PLR2004
        results = (
            Model.objects.filter(
                Q(name__icontains=query)
                | Q(codename__icontains=query)
                | Q(model_code__icontains=query)
                | Q(marketing_name__icontains=query)
                | Q(also_known_as__icontains=query)
                | Q(chipset__icontains=query)
                | Q(brand__name__icontains=query),
                is_active=True,
            )
            .select_related("brand")
            .order_by("brand__name", "name")[:50]
        )

    context = {"results": results, "query": query}

    if request.headers.get("HX-Request"):
        return render(request, "devices/fragments/search_results.html", context)
    return render(request, "devices/search.html", context)


@require_GET
def brand_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Public brand page — list all models for a brand."""
    from apps.firmwares.models import Brand, OfficialFirmware

    brand = get_object_or_404(Brand, slug=slug)
    models = brand.models.filter(is_active=True).order_by("-release_date", "name")  # type: ignore[attr-defined]
    search_query = request.GET.get("q", "").strip()
    sort_by = request.GET.get("sort", "newest")
    filter_status = request.GET.get("status", "")

    if search_query:
        from django.db.models import Q

        models = models.filter(
            Q(name__icontains=search_query)
            | Q(codename__icontains=search_query)
            | Q(model_code__icontains=search_query)
            | Q(marketing_name__icontains=search_query)
            | Q(chipset__icontains=search_query)
        )

    if filter_status:
        models = models.filter(status=filter_status)

    # Sort
    if sort_by == "name":
        models = models.order_by("name")
    elif sort_by == "oldest":
        models = models.order_by("release_date", "name")
    else:  # newest (default)
        models = models.order_by("-release_date", "name")

    # Annotate with firmware count
    models = models.annotate(firmware_count=Count("official_firmwares"))

    # Brand stats
    total_firmwares = OfficialFirmware.objects.filter(
        brand=brand, is_active=True
    ).count()

    context = {
        "brand": brand,
        "models": models,
        "search_query": search_query,
        "sort_by": sort_by,
        "filter_status": filter_status,
        "total_firmwares": total_firmwares,
        "breadcrumb": [{"label": "Devices", "url": "/devices/"}],
    }

    if request.headers.get("HX-Request"):
        return render(request, "devices/fragments/model_list.html", context)
    return render(request, "devices/brand_detail.html", context)


@require_GET
def model_detail(
    request: HttpRequest, brand_slug: str, model_slug: str
) -> HttpResponse:
    """Public model page — show specs, firmware files, and device info.

    MiFirm / SamFW / LGRom inspired: device header with image + codename,
    quick spec badges, firmware grouped by region tabs & type tabs,
    full specs grid, variant/region cards.
    """
    from apps.firmwares.models import (
        Brand,
        EngineeringFirmware,
        GSMArenaDevice,
        ModifiedFirmware,
        OfficialFirmware,
        OtherFirmware,
        ReadbackFirmware,
    )

    brand = get_object_or_404(Brand, slug=brand_slug)
    model = get_object_or_404(brand.models, slug=model_slug)  # type: ignore[attr-defined]
    variants = model.variants.filter(is_active=True).order_by("name")

    # Try to find GSMArena specs for this model
    gsm_device = GSMArenaDevice.objects.filter(
        brand__iexact=brand.name,
        model_name__iexact=model.name,
        review_status="approved",
    ).first()

    # Collect firmware files across all types for this model
    firmware_types = [
        (OfficialFirmware, "Official"),
        (EngineeringFirmware, "Engineering"),
        (ReadbackFirmware, "Readback"),
        (ModifiedFirmware, "Modified"),
        (OtherFirmware, "Other"),
    ]
    firmwares: list[dict[str, object]] = []
    for fw_model, fw_label in firmware_types:
        for fw in fw_model.objects.filter(model=model, is_active=True).select_related(
            "variant", "brand"
        ):
            firmwares.append(
                {
                    "id": str(fw.id),
                    "type": fw_label,
                    "file_name": fw.original_file_name,
                    "file_size": fw.file_size,
                    "android_version": fw.android_version or "",
                    "security_patch": fw.security_patch or "",
                    "build_number": fw.build_number or "",
                    "chipset": fw.chipset or "",
                    "variant_name": fw.variant.name if fw.variant else "Global",
                    "variant_region": (fw.variant.region if fw.variant else "Global"),
                    "is_verified": fw.is_verified,
                    "is_password_protected": fw.is_password_protected,
                    "download_count": fw.download_count,
                    "created_at": fw.created_at,
                }
            )

    # Build region → firmware grouping for MiFirm-style region tabs
    region_groups: dict[str, list[dict[str, object]]] = {}
    type_groups: dict[str, list[dict[str, object]]] = {}
    for fw in firmwares:
        region = str(fw.get("variant_region", "Global"))
        fw_type = str(fw.get("type", "Other"))
        region_groups.setdefault(region, []).append(fw)
        type_groups.setdefault(fw_type, []).append(fw)

    # Sorted region names for tabs
    region_names = sorted(region_groups.keys())
    type_names = sorted(type_groups.keys())

    # Also build device name aliases (different naming conventions)
    device_names = _collect_device_names(model, gsm_device)

    # Resolve chipset: model field first, then GSMArena
    chipset = model.chipset or (gsm_device.chipset if gsm_device else "")
    os_version = model.os_version or (gsm_device.os_version if gsm_device else "")
    image_url = model.image_url or (gsm_device.image_url if gsm_device else "")

    context = {
        "brand": brand,
        "model": model,
        "variants": variants,
        "gsm_device": gsm_device,
        "specs": gsm_device.specs if gsm_device else {},
        "firmwares": firmwares,
        "firmware_count": len(firmwares),
        "region_groups": region_groups,
        "region_names": region_names,
        "type_groups": type_groups,
        "type_names": type_names,
        "device_names": device_names,
        "chipset": chipset,
        "os_version": os_version,
        "image_url": image_url,
        "breadcrumb": [
            {"label": "Devices", "url": "/devices/"},
            {"label": brand.name, "url": f"/devices/{brand.slug}/"},
        ],
    }
    return render(request, "devices/model_detail.html", context)


def _collect_device_names(
    model: object, gsm_device: GSMArenaDevice | None
) -> list[dict[str, str]]:
    """Collect all known names/identifiers for a device.

    Different brands use different naming schemes:
    - Samsung: Marketing name (Galaxy A05s), Model code (SM-A057F/DS)
    - Xiaomi: Marketing name (Redmi Note 13 Pro), Code name (garnet), Model (23090RA98G)
    - Google: Marketing name (Pixel 8), Code name (shiba), Model (GKWS6)
    - Huawei: Marketing name (P60 Pro), Model code (MNA-AL00)
    - OnePlus: Marketing name (12), Code name (waffle), Model (CPH2583)
    """
    names: list[dict[str, str]] = []

    # Primary marketing name
    if hasattr(model, "marketing_name") and model.marketing_name:  # type: ignore[union-attr]
        if model.marketing_name != model.name:  # type: ignore[union-attr]
            names.append({"label": "Marketing Name", "value": model.marketing_name})  # type: ignore[union-attr]

    # Codename
    if hasattr(model, "codename") and model.codename:  # type: ignore[union-attr]
        names.append({"label": "Codename", "value": model.codename})  # type: ignore[union-attr]

    # Primary model code
    if hasattr(model, "model_code") and model.model_code:  # type: ignore[union-attr]
        names.append({"label": "Model Code", "value": model.model_code})  # type: ignore[union-attr]

    # All model codes from the model itself
    if hasattr(model, "model_codes") and model.model_codes:  # type: ignore[union-attr]
        codes_str = ", ".join(model.model_codes)  # type: ignore[union-attr]
        names.append({"label": "Model Numbers", "value": codes_str})

    # Also known as
    if hasattr(model, "also_known_as") and model.also_known_as:  # type: ignore[union-attr]
        names.append({"label": "Also Known As", "value": model.also_known_as})  # type: ignore[union-attr]

    # GSMArena enrichment (only if not already covered by model fields)
    if gsm_device:
        if gsm_device.marketed_as and not (
            hasattr(model, "also_known_as") and model.also_known_as  # type: ignore[union-attr]
        ):
            if gsm_device.marketed_as != getattr(model, "name", ""):
                names.append(
                    {"label": "Also Known As", "value": gsm_device.marketed_as}
                )

        if gsm_device.model_codes and not (
            hasattr(model, "model_codes") and model.model_codes  # type: ignore[union-attr]
        ):
            names.append({"label": "Model Numbers", "value": gsm_device.model_codes})

    return names


@login_required
def my_devices(request: HttpRequest) -> HttpResponse:
    devices = Device.objects.filter(user=request.user).order_by("-last_seen_at")[:20]
    device_count = devices.count()

    # Quota info: per-user override or global default
    user_quota = UserDeviceQuota.objects.filter(user=request.user).first()
    try:
        config = DeviceConfig.get_solo()  # type: ignore[attr-defined]
        max_default = config.max_devices_default or 5
    except Exception:
        max_default = 5

    max_devices = (
        user_quota.max_devices
        if (user_quota and user_quota.max_devices)
        else max_default
    )
    quota_window = user_quota.window if user_quota else "6m"
    last_reset = user_quota.last_reset_at if user_quota else None

    return render(
        request,
        "devices/my_devices.html",
        {
            "devices": devices,
            "device_count": device_count,
            "max_devices": max_devices,
            "quota_window": quota_window,
            "last_reset": last_reset,
            "quota_remaining": max(0, max_devices - device_count),
        },
    )


@staff_member_required(login_url="admin:login")
def device_events(request: HttpRequest) -> HttpResponse:
    events = DeviceEvent.objects.select_related("device").order_by("-created_at")[:50]
    return render(request, "devices/events.html", {"events": events})


# ---------------------------------------------------------------------
# Device payload API (collect JS fingerprint and store in session)
# ---------------------------------------------------------------------


@require_POST
@csrf_protect
def device_payload_view(request: HttpRequest) -> JsonResponse:
    """
    Collect device fingerprint data from JS and store in session.
    SECURITY: Requires CSRF token to prevent malicious fingerprint injection.
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid json"}, status=400)

    allowed = {
        "screen",
        "pixel_ratio",
        "timezone",
        "cores",
        "device_memory",
        "touch_points",
        "languages",
        "gpu_vendor",
        "gpu_renderer",
    }
    clean = {k: data.get(k, "") for k in allowed}
    request.session["device_payload"] = clean
    request.session.modified = True

    # -----------------------------------------------------------------
    # Fix for "Creating a lot of devices" / Duplication
    # -----------------------------------------------------------------
    # If a device was created during the initial page load (without payload),
    # it has a "Weak" fingerprint. Now that we have the payload, we should
    # migrate that "Weak" device to the "Strong" fingerprint instead of
    # letting the next request create a duplicate "Strong" device.
    if request.user.is_authenticated:
        try:
            ua = request.META.get("HTTP_USER_AGENT", "")
            # 1. Calculate Weak Fingerprint (Empty payload)
            weak_fp, _ = make_os_fingerprint(request.user.pk, ua, {})  # type: ignore[arg-type]

            # 2. Calculate Strong Fingerprint (With current payload)
            strong_fp, _ = make_os_fingerprint(request.user.pk, ua, clean)  # type: ignore[arg-type]

            # Only migrate if they are different (i.e., payload actually adds entropy)
            if weak_fp != strong_fp:
                # Find the weak device
                weak_device = Device.objects.filter(
                    user=request.user, os_fingerprint=weak_fp
                ).first()
                strong_device = Device.objects.filter(
                    user=request.user, os_fingerprint=strong_fp
                ).first()

                if weak_device:
                    if not strong_device:
                        # Case 1: Weak exists, Strong does not -> Upgrade Weak to Strong
                        weak_device.os_fingerprint = strong_fp
                        current_meta = (
                            weak_device.metadata
                            if isinstance(weak_device.metadata, dict)
                            else {}
                        )
                        weak_device.metadata = {**current_meta, "payload": clean}
                        weak_device.save(update_fields=["os_fingerprint", "metadata"])
                    else:
                        # Case 2: Both exist (Duplicate scenario) -> Delete Weak, keep Strong
                        # This cleans up the "two devices" issue automatically
                        weak_device.delete()
        except Exception as e:
            import logging

            logging.getLogger(__name__).debug(
                f"Device fingerprint migration skipped: {e}"
            )

    return JsonResponse({"ok": True})


@require_POST
@csrf_protect
@login_required
def acknowledge_new_device(request: HttpRequest) -> HttpResponse:
    """
    Register the device as trusted and clear the popup.
    Handles fingerprint upgrades (Weak -> Strong) if payload is available.

    The frontend sends the os_fingerprint from the popup session data,
    which helps us identify the correct device even if fingerprint changed.

    If dismiss_only=true, just mark the popup as shown without trusting the device.
    """
    import logging

    from apps.devices.models import Device
    from apps.devices.services import resolve_or_create_device
    from apps.devices.utils.device_fingerprint import make_os_fingerprint

    logger = logging.getLogger(__name__)

    # Parse request body for os_fingerprint hint from frontend
    frontend_fp = None
    dismiss_only = False
    try:
        data = json.loads(request.body.decode("utf-8"))
        frontend_fp = data.get("os_fingerprint", "").strip()
        dismiss_only = data.get("dismiss_only", False)
    except Exception:  # noqa: S110
        pass

    # Handle dismiss-only requests (user clicked X to close popup)
    if dismiss_only:
        # Just mark the device as shown so popup doesn't reappear
        if frontend_fp:
            shown_devices = set(request.session.get("devices_popup_shown", []))
            shown_devices.add(frontend_fp)
            request.session["devices_popup_shown"] = list(shown_devices)
        # Clear the popup session data
        for key in ["new_device_popup", "pending_device_prompt_uuid"]:
            if key in request.session:
                del request.session[key]
        request.session.modified = True
        return JsonResponse({"ok": True, "message": "Popup dismissed"})

    # 1. Identify the pending device
    # Priority: frontend fingerprint > session fingerprint
    pending_uuid = frontend_fp or request.session.get("pending_device_prompt_uuid")
    pending_device = None
    if pending_uuid:
        pending_device = Device.objects.filter(
            user=request.user, os_fingerprint=pending_uuid
        ).first()

    # 2. Calculate the current (Strong) fingerprint
    payload = getattr(request, "device_payload", {}) or {}
    ua = request.META.get("HTTP_USER_AGENT", "")
    current_fp, _ = make_os_fingerprint(request.user.pk, ua, payload)  # type: ignore[arg-type]

    final_device = pending_device

    # 3. Handle fingerprint upgrade scenario (Weak -> Strong)
    if pending_device and pending_device.os_fingerprint != current_fp:
        logger.debug(
            f"Fingerprint mismatch: pending={pending_device.os_fingerprint[:16]}... current={current_fp[:16]}..."
        )

        # Check if the "Strong" device already exists
        strong_device = Device.objects.filter(
            user=request.user, os_fingerprint=current_fp
        ).first()

        if strong_device:
            # Strong device exists. Use it and delete the weak one.
            logger.debug(
                f"Upgrading: deleting weak device {pending_device.id}, using strong device {strong_device.id}"
            )
            pending_device.delete()
            final_device = strong_device
        else:
            # Strong device doesn't exist. Upgrade the pending device's fingerprint.
            logger.debug(f"Upgrading device {pending_device.id} fingerprint to strong")
            pending_device.os_fingerprint = current_fp
            if payload:
                meta = pending_device.metadata or {}
                if isinstance(meta, dict):
                    meta["payload"] = payload
                else:
                    meta = {"payload": payload}
                pending_device.metadata = meta
            pending_device.save(update_fields=["os_fingerprint", "metadata"])
            final_device = pending_device

    # 4. Fallback: If no pending device found, try current fingerprint or resolve/create
    if not final_device:
        # Try to find device by current fingerprint
        final_device = Device.objects.filter(
            user=request.user, os_fingerprint=current_fp
        ).first()

        if not final_device:
            try:
                final_device, _, _ = resolve_or_create_device(
                    request, request.user, service_name="acknowledge"
                )
            except Exception as e:
                logger.warning(f"Failed to resolve/create device: {e}")

    # 5. Trust the final device
    if final_device:
        final_device.is_trusted = True
        final_device.save(update_fields=["is_trusted"])
        logger.info(
            f"Device {final_device.id} marked as trusted for user {request.user.pk}"
        )
    else:
        logger.warning(f"No device found to trust for user {request.user.pk}")
        return JsonResponse({"ok": False, "error": "Device not found"}, status=400)

    # 6. Clear session flags and mark device as popup-shown
    for key in ["new_device_popup", "pending_device_prompt_uuid"]:
        if key in request.session:
            del request.session[key]

    # Mark this device fingerprint as having been shown popup
    # Prevents the popup from re-appearing for this device
    if final_device:
        shown_devices = set(request.session.get("devices_popup_shown", []))
        shown_devices.add(final_device.os_fingerprint)
        request.session["devices_popup_shown"] = list(shown_devices)

    request.session.modified = True

    return JsonResponse({"ok": True, "message": "Device registered successfully"})
