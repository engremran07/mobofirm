"""
Public views for firmware browsing, detail, and download pages.
"""

import hashlib
import hmac
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction as db_transaction
from django.db.models import Count, F, Q
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

if TYPE_CHECKING:
    pass

from .download_service import (
    _get_download_config,
    check_download_allowed,
    check_hotlink,
    complete_ad_gate,
    create_download_token,
    generate_signed_url,
    sign_token,
    validate_download_token,
    verify_signature,
)
from .models import (
    Brand,
    DownloadSession,
    DownloadToken,
    EngineeringFirmware,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    ReadbackFirmware,
    Variant,
)

logger = logging.getLogger(__name__)

# Firmware type mapping
FIRMWARE_TYPES = {
    "official": OfficialFirmware,
    "officialfirmware": OfficialFirmware,
    "engineering": EngineeringFirmware,
    "engineeringfirmware": EngineeringFirmware,
    "readback": ReadbackFirmware,
    "readbackfirmware": ReadbackFirmware,
    "modified": ModifiedFirmware,
    "modifiedfirmware": ModifiedFirmware,
    "other": OtherFirmware,
    "otherfirmware": OtherFirmware,
}

FIRMWARE_TYPE_DISPLAY = {
    "official": "Official",
    "officialfirmware": "Official",
    "engineering": "Engineering",
    "engineeringfirmware": "Engineering",
    "readback": "Readback",
    "readbackfirmware": "Readback",
    "modified": "Modified",
    "modifiedfirmware": "Modified",
    "other": "Other",
    "otherfirmware": "Other",
}


def get_firmware_model(firmware_type: str) -> type | None:  # type: ignore[type-arg]
    """Get the firmware model class from type string."""
    return FIRMWARE_TYPES.get(firmware_type.lower())


def get_all_firmwares_for_model(model_obj: Model) -> list:  # type: ignore[type-arg]
    """Get all firmware types for a given device model."""
    firmwares = []
    for type_key, model_class in [
        ("official", OfficialFirmware),
        ("engineering", EngineeringFirmware),
        ("readback", ReadbackFirmware),
        ("modified", ModifiedFirmware),
        ("other", OtherFirmware),
    ]:
        qs = (
            model_class.objects.filter(model=model_obj, is_active=True)
            .select_related("brand", "model", "variant")
            .order_by("-created_at")
        )
        for fw in qs:
            fw.firmware_type = type_key
            fw.firmware_type_display = FIRMWARE_TYPE_DISPLAY[type_key]
            firmwares.append(fw)
    return firmwares


# ============================================================
# Library Hub & Universal Search (consolidated from apps.devices)
# ============================================================


@require_GET
def library_hub(request: HttpRequest) -> HttpResponse:
    """Multi-brand firmware library hub — main landing page for browsing.

    Shows aggregate stats, featured brands, full brand grid with model counts,
    and universal search across all brands/models/codenames.
    """
    search_query = request.GET.get("q", "").strip()
    brands = Brand.objects.annotate(model_count=Count("models")).order_by("name")

    # Featured brands first, then alphabetical
    featured_brands = brands.filter(is_featured=True)

    if search_query:
        brands = brands.filter(name__icontains=search_query)

    # Aggregate stats for the whole platform
    total_models = Model.objects.filter(is_active=True).count()
    total_firmwares = OfficialFirmware.objects.filter(is_active=True).count()

    from .models import GSMArenaDevice

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
        return render(request, "firmwares/fragments/brand_list.html", context)
    return render(request, "firmwares/library_hub.html", context)


@require_GET
def universal_search(request: HttpRequest) -> HttpResponse:
    """Universal search across all brands, models, codenames, model codes.

    Returns model results matching the query, grouped by brand.
    Works as HTMX fragment or standalone page.
    """
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
        return render(request, "firmwares/fragments/search_results.html", context)
    return render(request, "firmwares/search.html", context)


# ============================================================
# Browse / List Views
# ============================================================


@require_GET
def firmware_browse(request: HttpRequest) -> HttpResponse:
    """Main firmware browsing page with filters."""
    # Get all brands with firmware count
    brands = Brand.objects.all().order_by("name")

    # Get filter parameters
    brand_slug = request.GET.get("brand")
    firmware_type = request.GET.get("type", "all")
    chipset = request.GET.get("chipset")
    search = request.GET.get("q", "").strip()

    # Build queryset based on type
    if firmware_type == "all" or not firmware_type:
        # Get from all types
        all_firmwares = []
        for type_key, model_class in FIRMWARE_TYPES.items():
            if type_key in ["official", "engineering", "readback", "modified", "other"]:
                qs = model_class.objects.filter(is_active=True).select_related(
                    "brand", "model", "variant"
                )

                if brand_slug:
                    qs = qs.filter(brand__slug=brand_slug)
                if chipset:
                    qs = qs.filter(chipset__icontains=chipset)
                if search:
                    qs = qs.filter(
                        Q(original_file_name__icontains=search)
                        | Q(model__name__icontains=search)
                        | Q(brand__name__icontains=search)
                        | Q(chipset__icontains=search)
                    )

                for fw in qs:
                    fw.firmware_type = type_key
                    fw.firmware_type_display = FIRMWARE_TYPE_DISPLAY[type_key]
                    all_firmwares.append(fw)

        # Sort by created_at
        all_firmwares.sort(key=lambda x: x.created_at, reverse=True)
        firmwares_list = all_firmwares
    else:
        model_class = get_firmware_model(firmware_type)
        if not model_class:
            raise Http404("Invalid firmware type")

        qs = model_class.objects.filter(is_active=True).select_related(
            "brand", "model", "variant"
        )

        if brand_slug:
            qs = qs.filter(brand__slug=brand_slug)
        if chipset:
            qs = qs.filter(chipset__icontains=chipset)
        if search:
            qs = qs.filter(
                Q(original_file_name__icontains=search)
                | Q(model__name__icontains=search)
                | Q(brand__name__icontains=search)
                | Q(chipset__icontains=search)
            )

        firmwares_list = list(qs.order_by("-created_at"))
        for fw in firmwares_list:
            fw.firmware_type = firmware_type
            fw.firmware_type_display = FIRMWARE_TYPE_DISPLAY.get(
                firmware_type, firmware_type
            )

    # Pagination
    paginator = Paginator(firmwares_list, 20)
    page = request.GET.get("page", 1)
    firmwares = paginator.get_page(page)

    # Get unique chipsets for filter
    chipsets = set()
    for type_key, model_class in FIRMWARE_TYPES.items():
        if type_key in ["official", "engineering", "readback", "modified", "other"]:
            for c in (
                model_class.objects.filter(is_active=True)
                .values_list("chipset", flat=True)
                .distinct()
            ):
                if c:
                    chipsets.add(c)

    context = {
        "firmwares": firmwares,
        "brands": brands,
        "chipsets": sorted(chipsets),
        "selected_brand": brand_slug,
        "selected_type": firmware_type,
        "selected_chipset": chipset,
        "search_query": search,
        "firmware_types": [
            ("all", "All Types"),
            ("official", "Official"),
            ("engineering", "Engineering"),
            ("readback", "Readback"),
            ("modified", "Modified"),
            ("other", "Other"),
        ],
    }
    return render(request, "firmwares/browse.html", context)


@require_GET
def brand_list(request: HttpRequest) -> HttpResponse:
    """List all brands grouped by alphabet."""
    brands = Brand.objects.all().order_by("name")

    # Add firmware counts and group by first letter
    total_firmwares = 0
    total_models = 0
    brands_by_letter = {}

    for brand in brands:
        brand.firmware_count = sum(  # type: ignore[attr-defined]
            [
                OfficialFirmware.objects.filter(brand=brand, is_active=True).count(),
                EngineeringFirmware.objects.filter(brand=brand, is_active=True).count(),
                ReadbackFirmware.objects.filter(brand=brand, is_active=True).count(),
                ModifiedFirmware.objects.filter(brand=brand, is_active=True).count(),
                OtherFirmware.objects.filter(brand=brand, is_active=True).count(),
            ]
        )
        brand.model_count = Model.objects.filter(brand=brand, is_active=True).count()  # type: ignore[attr-defined]
        total_firmwares += brand.firmware_count  # type: ignore[attr-defined]
        total_models += brand.model_count  # type: ignore[attr-defined]

        # Group by first letter
        first_letter = brand.name[0].upper() if brand.name else "#"
        if not first_letter.isalpha():
            first_letter = "#"
        if first_letter not in brands_by_letter:
            brands_by_letter[first_letter] = []
        brands_by_letter[first_letter].append(brand)

    # Sort letters alphabetically (# at the end)
    sorted_letters = sorted([l for l in brands_by_letter.keys() if l != "#"])  # noqa: E741
    if "#" in brands_by_letter:
        sorted_letters.append("#")

    # Create ordered dict of brands by letter
    grouped_brands = [(letter, brands_by_letter[letter]) for letter in sorted_letters]

    context = {
        "brands": brands,
        "grouped_brands": grouped_brands,
        "alphabet": sorted_letters,
        "total_firmwares": total_firmwares,
        "total_models": total_models,
    }
    return render(request, "firmwares/brand_list.html", context)


@require_GET
def brand_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Brand detail page with all models."""
    brand = get_object_or_404(Brand, slug=slug)
    models = Model.objects.filter(brand=brand, is_active=True).order_by("name")

    # Add firmware counts to models
    total_firmwares = 0
    for model in models:
        model.firmware_count = sum(  # type: ignore[attr-defined]
            [
                OfficialFirmware.objects.filter(model=model, is_active=True).count(),
                EngineeringFirmware.objects.filter(model=model, is_active=True).count(),
                ReadbackFirmware.objects.filter(model=model, is_active=True).count(),
                ModifiedFirmware.objects.filter(model=model, is_active=True).count(),
                OtherFirmware.objects.filter(model=model, is_active=True).count(),
            ]
        )
        total_firmwares += model.firmware_count  # type: ignore[attr-defined]

    # Get other brands for sidebar
    other_brands = Brand.objects.exclude(id=brand.id).order_by("name")[:12]  # type: ignore[attr-defined]

    context = {
        "brand": brand,
        "models": models,
        "total_firmwares": total_firmwares,
        "other_brands": other_brands,
    }
    return render(request, "firmwares/brand_detail.html", context)


@require_GET
def model_detail(
    request: HttpRequest, brand_slug: str, model_slug: str
) -> HttpResponse:
    """Model detail page with all firmwares."""
    brand = get_object_or_404(Brand, slug=brand_slug)
    model = get_object_or_404(Model, brand=brand, slug=model_slug)
    variants = Variant.objects.filter(model=model, is_active=True).order_by("name")

    # Get all firmwares for this model
    firmwares = get_all_firmwares_for_model(model)

    # Add file size display to each firmware
    for fw in firmwares:
        if fw.file_size:
            if fw.file_size >= 1073741824:
                fw.file_size_display = f"{fw.file_size / 1073741824:.2f} GB"
            elif fw.file_size >= 1048576:
                fw.file_size_display = f"{fw.file_size / 1048576:.2f} MB"
            else:
                fw.file_size_display = f"{fw.file_size / 1024:.2f} KB"
        else:
            fw.file_size_display = "Unknown"

    # Get available firmware types
    firmware_types = list({fw.firmware_type for fw in firmwares})

    context = {
        "brand": brand,
        "model": model,
        "variants": variants,
        "firmwares": firmwares,
        "total_firmwares": len(firmwares),
        "firmware_types": firmware_types,
    }
    return render(request, "firmwares/model_detail.html", context)


# ============================================================
# Firmware Detail View
# ============================================================


@require_GET
def firmware_detail(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Firmware detail page."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        raise Http404("Invalid firmware type")

    firmware = get_object_or_404(
        model_class.objects.select_related("brand", "model", "variant", "uploader"),
        id=firmware_id,
        is_active=True,
    )

    # Increment view count
    model_class.objects.filter(id=firmware_id).update(view_count=F("view_count") + 1)

    # Get related firmwares (same model, different files)
    related_firmwares = []
    if firmware.model:
        related_firmwares = get_all_firmwares_for_model(firmware.model)
        # Exclude current firmware
        related_firmwares = [
            f for f in related_firmwares if str(f.id) != str(firmware_id)
        ][:8]

    # Related solution/guide blog posts for this device model
    related_blog_posts: list = []
    if firmware.model:
        try:
            from apps.blog.models import (
                Post,  # lazy import — avoid circular
                PostStatus,
            )

            related_blog_posts = list(
                Post.objects.filter(
                    firmware_model=firmware.model,
                    status=PostStatus.PUBLISHED,
                ).order_by("-created_at")[:4]
            )
        except Exception:
            logger.debug("Related blog posts lookup failed", exc_info=True)

    # Format file size
    if firmware.file_size:
        if firmware.file_size >= 1073741824:  # 1 GB
            file_size_display = f"{firmware.file_size / 1073741824:.2f} GB"
        elif firmware.file_size >= 1048576:  # 1 MB
            file_size_display = f"{firmware.file_size / 1048576:.2f} MB"
        elif firmware.file_size >= 1024:  # 1 KB
            file_size_display = f"{firmware.file_size / 1024:.2f} KB"
        else:
            file_size_display = f"{firmware.file_size} bytes"
    else:
        file_size_display = "Unknown"

    context = {
        "firmware": firmware,
        "firmware_type": firmware_type,
        "firmware_type_display": FIRMWARE_TYPE_DISPLAY.get(
            firmware_type, firmware_type
        ),
        "related_firmwares": related_firmwares,
        "related_blog_posts": related_blog_posts,
        "file_size_display": file_size_display,
    }
    return render(request, "firmwares/firmware_detail.html", context)


# ============================================================
# Download Views
# ============================================================


@require_GET
def firmware_download(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Firmware download page with server-side gated countdown + ad-gate."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        raise Http404("Invalid firmware type")

    firmware = get_object_or_404(
        model_class.objects.select_related("brand", "model", "variant"),
        id=firmware_id,
        is_active=True,
    )

    # Hotlink protection
    if check_hotlink(request):
        raise Http404("Direct linking is not allowed.")

    # Check if download is allowed (login, rate limits)
    config = _get_download_config()
    allowed, reason = check_download_allowed(request, firmware)
    if not allowed:
        messages.error(request, reason)
        return redirect(
            "firmwares:firmware_detail",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    # Create a download token (server-side enforcement)
    token = create_download_token(firmware, request)
    signature = sign_token(token.token)

    # Format file size
    if firmware.file_size:
        if firmware.file_size >= 1073741824:
            file_size_display = f"{firmware.file_size / 1073741824:.2f} GB"
        elif firmware.file_size >= 1048576:
            file_size_display = f"{firmware.file_size / 1048576:.2f} MB"
        else:
            file_size_display = f"{firmware.file_size / 1024:.2f} KB"
    else:
        file_size_display = "Unknown"

    context = {
        "firmware": firmware,
        "firmware_type": firmware_type,
        "firmware_type_display": FIRMWARE_TYPE_DISPLAY.get(
            firmware_type, firmware_type
        ),
        "file_size_display": file_size_display,
        "download_token": token.token,
        "download_signature": signature,
        "countdown_seconds": config["countdown_seconds"],
        "ad_gate_enabled": config["ad_gate_enabled"],
        "ad_gate_seconds": config["ad_gate_seconds"],
        "gate_enabled": config["gate_enabled"],
    }
    return render(request, "firmwares/download.html", context)


@require_POST
def firmware_download_start(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Start actual download — validates token, enforces ad-gate, tracks session.

    FINDING-0002: Changed from @require_GET to @require_POST so the token and
    signature are passed in the POST body instead of URL query parameters.
    GET params appear in server logs, browser history, and HTTP Referer headers.

    FINDING-0001: Uses SELECT FOR UPDATE inside transaction.atomic() to
    atomically validate + consume the token, preventing TOCTOU race conditions
    where two concurrent requests with the same token both succeed.
    """
    from apps.core.utils.ip import get_client_ip as get_request_ip

    model_class = get_firmware_model(firmware_type)
    if not model_class:
        raise Http404("Invalid firmware type")

    firmware = get_object_or_404(model_class, id=firmware_id, is_active=True)

    # FINDING-0002: Read from POST body (not GET params).
    token_str = request.POST.get("token", "")
    sig = request.POST.get("sig", "")

    if not token_str or not sig:
        messages.error(request, "Invalid download link. Please try again.")
        return redirect(
            "firmwares:firmware_download",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    if not verify_signature(token_str, sig):
        messages.error(request, "Download link signature is invalid.")
        return redirect(
            "firmwares:firmware_download",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    # FINDING-0001: Atomically validate + consume the token inside SELECT FOR
    # UPDATE to prevent TOCTOU races where two concurrent requests both pass
    # the validation step and receive a successful download for one token.
    token_obj: DownloadToken | None = None
    error_code: str | None = None

    with db_transaction.atomic():
        try:
            token_obj = DownloadToken.objects.select_for_update().get(
                token=token_str, status=DownloadToken.Status.ACTIVE
            )
        except DownloadToken.DoesNotExist:
            error_code = "expired"

        if error_code is None and token_obj is not None:
            if token_obj.expires_at < timezone.now():
                token_obj.status = DownloadToken.Status.EXPIRED
                token_obj.save(update_fields=["status"])
                error_code = "expired"
            elif token_obj.firmware_id != firmware.pk:  # type: ignore[attr-defined]
                error_code = "invalid"
            elif token_obj.ad_gate_required and not token_obj.ad_gate_completed:
                error_code = "adgate"
            else:
                DownloadSession.objects.create(
                    token=token_obj,
                    user=token_obj.user,
                    ip=get_request_ip(request) or None,
                    user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
                    status=DownloadSession.Status.STARTED,
                )
                token_obj.status = DownloadToken.Status.USED
                token_obj.used_at = timezone.now()
                token_obj.save(update_fields=["status", "used_at"])

    if error_code == "expired" or token_obj is None:
        messages.error(request, "Download link has expired. Please try again.")
        return redirect(
            "firmwares:firmware_download",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    if error_code == "invalid":
        messages.error(request, "Invalid download token for this firmware.")
        return redirect(
            "firmwares:firmware_download",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    if error_code == "adgate":
        messages.error(request, "Please complete the ad gate first.")
        return redirect(
            "firmwares:firmware_download",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    # Increment download count
    model_class.objects.filter(id=firmware_id).update(
        download_count=F("download_count") + 1
    )

    logger.info(
        "Firmware download: %s by %s (token=%s)",
        firmware.original_file_name,
        request.user if request.user.is_authenticated else "anonymous",
        token_str[:10],
    )

    # Redirect to file
    if firmware.stored_file_path:
        if firmware.stored_file_path.startswith(("http://", "https://")):
            return redirect(firmware.stored_file_path)
        # FINDING-0019: serve locally stored file via HMAC-signed URL.
        signed_url = generate_signed_url(firmware, token_obj)
        if signed_url:
            return redirect(signed_url)

    messages.error(request, "File not available for download.")
    return redirect(
        "firmwares:firmware_detail",
        firmware_type=firmware_type,
        firmware_id=firmware_id,
    )


@require_POST
def firmware_ad_gate_complete(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> JsonResponse:
    """AJAX endpoint to mark ad-gate as completed for a download token."""
    token_str = request.POST.get("token", "")
    sig = request.POST.get("sig", "")

    if not token_str or not sig or not verify_signature(token_str, sig):
        return JsonResponse({"ok": False, "error": "Invalid token"}, status=400)

    token = validate_download_token(token_str)
    if not token:
        return JsonResponse({"ok": False, "error": "Token expired"}, status=400)

    ad_type = request.POST.get("ad_type", "video")
    if ad_type not in ("video", "banner", "interstitial"):
        ad_type = "video"

    complete_ad_gate(token, ad_type=ad_type)
    return JsonResponse({"ok": True})


@require_GET
def serve_local_file(request: HttpRequest, firmware_id: str) -> HttpResponseBase:
    """Serve a locally stored firmware file via HMAC-signed time-limited URL.

    FINDING-0019: generate_signed_url() previously returned None for all local
    files, making locally uploaded firmware inaccessible.  This view validates
    a short-lived HMAC signature and streams the file through Django (or
    X-Accel-Redirect in production) so the filesystem path is never exposed.

    URL params:
        token  — the DownloadToken.token value
        sig    — full HMAC-SHA256 hexdigest of token (from sign_token())
    """
    token_str = request.GET.get("token", "")
    sig = request.GET.get("sig", "")

    # Validate HMAC signature first (constant-time compare)
    if not token_str or not sig:
        raise Http404

    key = getattr(settings, "SECRET_KEY", "").encode()
    expected = hmac.new(key, token_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise Http404

    # Find the token and its firmware
    token_obj = DownloadToken.objects.filter(token=token_str).select_related().first()
    if not token_obj:
        raise Http404

    # Token must be active or used (used = download was already started legitimately)
    if token_obj.status not in (
        DownloadToken.Status.ACTIVE,
        DownloadToken.Status.USED,
    ):
        raise Http404

    # Must not be expired
    if token_obj.expires_at and token_obj.expires_at < timezone.now():
        raise Http404

    # Resolve the firmware record from the token's content_object
    firmware = token_obj.firmware_object  # type: ignore[attr-defined]
    if firmware is None:
        raise Http404

    file_path_str: str = getattr(firmware, "stored_file_path", "") or ""
    if not file_path_str or file_path_str.startswith(("http://", "https://")):
        raise Http404

    # Resolve the absolute path and ensure it stays inside MEDIA_ROOT
    media_root = Path(getattr(settings, "MEDIA_ROOT", "")).resolve()
    abs_path = (media_root / file_path_str).resolve()
    if not str(abs_path).startswith(str(media_root)):
        logger.warning(
            "serve_local_file: path traversal attempt blocked for firmware %s",
            firmware_id,
        )
        raise Http404

    if not abs_path.exists():
        logger.error(
            "serve_local_file: firmware file not found on disk: %s",
            abs_path,
        )
        raise Http404

    filename = os.path.basename(file_path_str)

    # Production: use X-Accel-Redirect (Nginx) for zero-copy streaming.
    # Development: use Django's FileResponse.
    if not getattr(settings, "DEBUG", True):
        # Strip MEDIA_ROOT prefix to get the internal Nginx path
        try:
            rel = abs_path.relative_to(media_root)
        except ValueError:
            raise Http404 from None
        internal_url = f"/protected-media/{rel.as_posix()}"
        response = HttpResponse()
        response["X-Accel-Redirect"] = internal_url
        response["Content-Type"] = "application/octet-stream"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    # Development / fallback: stream directly via Django
    from django.http import FileResponse

    file_handle = open(abs_path, "rb")  # noqa: WPS515 — FileResponse closes it
    response = FileResponse(file_handle, as_attachment=True, filename=filename)
    return response


# ============================================================
# API Endpoints for AJAX
# ============================================================


@require_GET
def api_firmware_stats(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> JsonResponse:
    """Get firmware stats (views, downloads)."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        return JsonResponse({"error": "Invalid firmware type"}, status=404)

    try:
        firmware = model_class.objects.get(id=firmware_id)
        return JsonResponse(
            {
                "views": firmware.view_count,
                "downloads": firmware.download_count,
            }
        )
    except model_class.DoesNotExist:
        return JsonResponse({"error": "Firmware not found"}, status=404)


@require_GET
def api_search_autocomplete(request: HttpRequest) -> JsonResponse:
    """
    Global search autocomplete API.
    Returns matching brands, models, variants, and firmwares.

    Searchable fields by type:
    - Brands: name, slug
    - Models: name, slug, marketing_name, model_code, description
    - Variants: name, slug, region, board_id, chipset
    - Firmwares: original_file_name, chipset, android_version, build_number,
                 + related brand/model/variant names and model codes
    """
    query = request.GET.get("q", "").strip()
    search_type = request.GET.get("type", "all")  # all, brands, models, firmwares, blog
    limit = min(int(request.GET.get("limit", 10)), 20)

    if len(query) < 2:
        return JsonResponse({"results": [], "query": query})

    results = []

    # Search Brands
    if search_type in ("all", "brands"):
        brands = Brand.objects.filter(
            Q(name__icontains=query) | Q(slug__icontains=query)
        ).order_by("name")[:limit]

        for brand in brands:
            model_count = brand.models.count()  # type: ignore[attr-defined]
            results.append(
                {
                    "type": "brand",
                    "id": brand.id,  # type: ignore[attr-defined]
                    "name": brand.name,
                    "slug": brand.slug,
                    "url": reverse("firmwares:brand_detail", args=[brand.slug]),
                    "icon": "building",
                    "subtitle": f"{model_count} model{'s' if model_count != 1 else ''}",
                }
            )

    # Search Models - includes marketing_name, model_code, description
    if search_type in ("all", "models"):
        models_qs = (
            Model.objects.filter(
                Q(name__icontains=query)
                | Q(slug__icontains=query)
                | Q(marketing_name__icontains=query)
                | Q(model_code__icontains=query)
                | Q(description__icontains=query)
                | Q(brand__name__icontains=query),
                is_active=True,
            )
            .select_related("brand")
            .order_by("name")[:limit]
        )

        for model in models_qs:
            # Build informative subtitle
            subtitle_parts = [model.brand.name]
            if model.model_code:
                subtitle_parts.append(model.model_code)

            results.append(
                {
                    "type": "model",
                    "id": model.id,  # type: ignore[attr-defined]
                    "name": model.marketing_name or model.name,
                    "slug": model.slug,
                    "url": reverse(
                        "firmwares:model_detail", args=[model.brand.slug, model.slug]
                    ),
                    "icon": "smartphone",
                    "subtitle": " • ".join(subtitle_parts),
                    "brand": model.brand.name,
                    "model_code": model.model_code or "",
                    "marketing_name": model.marketing_name or "",
                }
            )

    # Search Variants - includes region, board_id, chipset
    if search_type in ("all", "variants"):
        from .models import Variant

        variants_qs = (
            Variant.objects.filter(
                Q(name__icontains=query)
                | Q(slug__icontains=query)
                | Q(region__icontains=query)
                | Q(board_id__icontains=query)
                | Q(chipset__icontains=query)
                | Q(model__name__icontains=query)
                | Q(model__model_code__icontains=query)
                | Q(model__brand__name__icontains=query),
                is_active=True,
            )
            .select_related("model", "model__brand")
            .order_by("name")[:limit]
        )

        for variant in variants_qs:
            # Build informative subtitle
            subtitle_parts = []
            if variant.model and variant.model.brand:
                subtitle_parts.append(
                    f"{variant.model.brand.name} {variant.model.name}"
                )
            if variant.chipset:
                subtitle_parts.append(variant.chipset)
            if variant.region:
                subtitle_parts.append(variant.region)

            results.append(
                {
                    "type": "variant",
                    "id": variant.id,  # type: ignore[attr-defined]
                    "name": variant.name,
                    "slug": variant.slug,
                    "url": reverse(
                        "firmwares:model_detail",
                        args=[
                            variant.model.brand.slug
                            if variant.model and variant.model.brand
                            else "",
                            variant.model.slug if variant.model else "",
                        ],
                    ),
                    "icon": "layers",
                    "subtitle": " • ".join(subtitle_parts)
                    if subtitle_parts
                    else "Variant",
                    "chipset": variant.chipset or "",
                    "region": variant.region or "",
                }
            )

    # Search Firmwares - comprehensive search across all firmware fields
    if search_type in ("all", "firmwares"):
        remaining = limit - len(results) if search_type == "all" else limit
        if remaining > 0:
            for type_key, model_class in [
                ("official", OfficialFirmware),
                ("engineering", EngineeringFirmware),
            ]:
                if remaining <= 0:
                    break

                # Comprehensive firmware search including:
                # - File name, chipset, android version, build number
                # - Related brand name
                # - Related model name, model_code, marketing_name
                # - Related variant name, region, chipset
                firmwares = model_class.objects.filter(
                    Q(original_file_name__icontains=query)
                    | Q(chipset__icontains=query)
                    | Q(android_version__icontains=query)
                    | Q(build_number__icontains=query)
                    | Q(brand__name__icontains=query)
                    | Q(model__name__icontains=query)
                    | Q(model__model_code__icontains=query)
                    | Q(model__marketing_name__icontains=query)
                    | Q(variant__name__icontains=query)
                    | Q(variant__region__icontains=query)
                    | Q(variant__chipset__icontains=query),
                    is_active=True,
                ).select_related("brand", "model", "variant")[:remaining]

                for fw in firmwares:
                    # Build informative subtitle
                    subtitle_parts = []
                    if fw.brand:
                        subtitle_parts.append(fw.brand.name)
                    if fw.model:
                        subtitle_parts.append(fw.model.name)
                    if fw.variant:
                        subtitle_parts.append(fw.variant.name)
                    if fw.chipset:
                        subtitle_parts.append(fw.chipset)

                    results.append(
                        {
                            "type": "firmware",
                            "id": str(fw.id),
                            "name": fw.original_file_name[:60]
                            + ("..." if len(fw.original_file_name) > 60 else ""),
                            "url": reverse(
                                "firmwares:firmware_detail", args=[type_key, fw.id]
                            ),
                            "icon": "cpu",
                            "subtitle": " • ".join(subtitle_parts[:3])
                            if subtitle_parts
                            else type_key.title(),
                            "firmware_type": type_key,
                            "chipset": fw.chipset or "",
                            "android_version": fw.android_version or "",
                        }
                    )
                    remaining -= 1

    # Search Blog Posts
    if search_type in ("all", "blog"):
        remaining = limit - len(results) if search_type == "all" else limit
        if remaining > 0:
            try:
                from django.utils import timezone as tz

                from apps.blog.models import Post

                # Search by title, summary, category name, and tags
                # Note: Body content search is intentionally excluded for autocomplete
                # as it's too expensive and returns too many irrelevant results.
                # Full-text body search should be done on the actual search results page.
                posts = (
                    Post.objects.filter(
                        Q(title__icontains=query)
                        | Q(summary__icontains=query)
                        | Q(category__name__icontains=query)
                        | Q(tags__name__icontains=query),
                        status="published",
                        publish_at__lte=tz.now(),
                    )
                    .select_related("category")
                    .prefetch_related("tags")
                    .distinct()
                    .order_by("-published_at")[:remaining]
                )

                for post in posts:
                    # Get tag names for subtitle
                    tag_names = [t.name for t in post.tags.all()[:3]]
                    subtitle_parts = []
                    if post.category:
                        subtitle_parts.append(post.category.name)
                    if tag_names:
                        subtitle_parts.append(", ".join(tag_names))
                    if post.published_at:
                        subtitle_parts.append(post.published_at.strftime("%b %d, %Y"))

                    results.append(
                        {
                            "type": "blog",
                            "id": post.id,  # type: ignore[attr-defined]
                            "name": post.title,
                            "slug": post.slug,
                            "url": reverse("blog:post_detail", args=[post.slug]),
                            "icon": "file-text",
                            "subtitle": " • ".join(subtitle_parts)
                            if subtitle_parts
                            else "Blog Post",
                        }
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("Blog search failed: %s", e)

    return JsonResponse(
        {
            "results": results[:limit],
            "query": query,
            "count": len(results),
        }
    )
