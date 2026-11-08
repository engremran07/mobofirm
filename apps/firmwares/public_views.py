"""
Public views for firmware browsing, detail, and download pages.

Every listing goes through ``catalog.get_public_firmware_queryset`` (active and
approved firmware only). Downloads are gated server-side: signed page token,
countdown, optional ad gate, single-use consumption and short-lived signed links.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import CharField, Count, F, Q, QuerySet, Value
from django.http import (
    FileResponse,
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

from . import catalog
from .access import (
    check_download_access,
    finalize_served_download,
)
from .catalog import (
    FIRMWARE_TYPE_DISPLAY,
    FIRMWARE_TYPES,
    PUBLIC_FIRMWARE_TYPES,
    get_all_firmwares_for_model,
    get_firmware_model,
    get_public_firmware_queryset,
    group_by_category,
    label_firmware,
    public_count_by_type,
    public_counts,
    total_public_firmware,
)
from .download_service import (
    TokenError,
    _get_download_config,
    check_download_allowed,
    check_hotlink,
    complete_ad_gate,
    consume_download_token,
    countdown_remaining,
    create_download_token,
    generate_signed_url,
    seconds_until,
    sign_token,
    validate_download_token,
    verify_link,
    verify_signature,
)
from .models import (
    Brand,
    Chipset,
    DownloadSession,
    DownloadToken,
    FirmwarePackageFormat,
    FlashingTool,
    GeneratedFlashingGuide,
    Model,
    Variant,
)
from .services import storage_root
from .sources import validate_external_url
from .tracking_models import FirmwareDownloadAttempt, FirmwareView

logger = logging.getLogger(__name__)

# Re-exported for callers that import them from this module.
__all__ = [
    "FIRMWARE_TYPES",
    "FIRMWARE_TYPE_DISPLAY",
    "PUBLIC_FIRMWARE_TYPES",
    "get_all_firmwares_for_model",
    "get_firmware_model",
    "get_public_firmware_queryset",
]

BRAND_MODEL_PAGE_SIZE = 24
CHIPSET_PAGE_SIZE = 48
BROWSE_PAGE_SIZE = 20


# ============================================================
# Shared helpers
# ============================================================


def _published_posts(**filters):
    """Live, published blog posts matching the filters (lazy blog import)."""
    from apps.blog.models import Post, PostStatus

    return Post.objects.filter(
        status=PostStatus.PUBLISHED, publish_at__lte=timezone.now(), **filters
    )


def _automated_post(kind: str, target):
    """The live automated post for a brand, model or chipset, if any."""
    from .models import AutomatedBlogLink

    link = (
        AutomatedBlogLink.objects.select_related("post")
        .filter(kind=kind, **{kind: target})
        .first()
    )
    if link is None:
        return None
    return _published_posts(pk=link.post_id).first()


def _guides(**filters):
    """Published flashing-guide posts matching the filters."""
    from apps.blog.models import PostStatus

    return (
        GeneratedFlashingGuide.objects.filter(
            post__status=PostStatus.PUBLISHED,
            post__publish_at__lte=timezone.now(),
            **filters,
        )
        .select_related("post", "brand", "model", "template")
        .order_by("-post__published_at", "-created_at")
    )


def _tools_for(brand=None, chipsets=None, limit: int = 12) -> list:
    condition = Q()
    if brand is not None:
        condition |= Q(supported_brands=brand)
    if chipsets:
        condition |= Q(chipsets__in=chipsets)
    if not condition:
        return []
    return list(
        FlashingTool.objects.filter(condition, is_active=True)
        .select_related("category")
        .distinct()
        .order_by("-is_featured", "name")[:limit]
    )


def _category_summary(*conditions, **filters) -> list[tuple[str, str, int]]:
    counts = public_count_by_type(*conditions, **filters)
    return [
        (key, FIRMWARE_TYPE_DISPLAY[key], counts[key])
        for key, _cls in PUBLIC_FIRMWARE_TYPES
        if counts[key]
    ]


def _external_host(firmware) -> str:
    return (
        urlsplit(firmware.external_url).hostname or "" if firmware.is_external else ""
    )


# ============================================================
# Library Hub & Universal Search
# ============================================================


@require_GET
def library_hub(request: HttpRequest) -> HttpResponse:
    """Multi-brand firmware library hub — main landing page for browsing."""
    search_query = request.GET.get("q", "").strip()
    brands = Brand.objects.annotate(
        model_count=Count("models", filter=Q(models__is_active=True))
    ).order_by("name")
    featured_brands = brands.filter(is_featured=True)
    if search_query:
        brands = brands.filter(name__icontains=search_query)

    context = {
        "brands": brands,
        "featured_brands": featured_brands,
        "search_query": search_query,
        "total_brands": Brand.objects.count(),
        "total_models": Model.objects.filter(is_active=True).count(),
        "total_firmwares": total_public_firmware(),
        "total_chipsets": Chipset.objects.count(),
        "latest_models": Model.objects.filter(is_active=True)
        .select_related("brand")
        .order_by("-created_at", "brand__name", "name")[:6],
    }

    if request.headers.get("HX-Request"):
        return render(request, "firmwares/fragments/brand_list.html", context)
    context["home_widgets"] = _home_widgets()
    return render(request, "firmwares/library_hub.html", context)


def _home_widgets() -> dict[str, list]:
    """Homepage blocks fed by the optional pages app (latest firmware, posts, tools)."""
    from django.apps import apps as django_apps

    from apps.core.app_service import app_enabled

    if not (django_apps.is_installed("apps.pages") and app_enabled("pages")):
        return {}
    from apps.pages import widgets

    return {
        "firmwares": widgets.latest_firmwares(limit=6),
        "posts": widgets.latest_posts(limit=4),
        "tools": widgets.latest_tools(limit=6),
    }


@require_GET
def universal_search(request: HttpRequest) -> HttpResponse:
    """Universal search across brands, models, codenames, model codes and chipsets."""
    query = request.GET.get("q", "").strip()
    results: Iterable[Model] = []
    if len(query) >= 2:
        results = (
            Model.objects.filter(
                Q(name__icontains=query)
                | Q(codename__icontains=query)
                | Q(model_code__icontains=query)
                | Q(marketing_name__icontains=query)
                | Q(also_known_as__icontains=query)
                | Q(chipset__icontains=query)
                | Q(chipset_ref__name__icontains=query)
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
# Browse / list views
# ============================================================


def _filtered_firmware_queryset(
    model_class, *, brand_slug, chipset, package_format, search
):
    queryset = get_public_firmware_queryset(model_class)
    if brand_slug:
        queryset = queryset.filter(brand__slug=brand_slug)
    if chipset:
        queryset = queryset.filter(
            Q(chipset__icontains=chipset)
            | Q(model__chipset_ref__slug=chipset)
            | Q(variant__chipset_ref__slug=chipset)
        )
    if package_format:
        queryset = queryset.filter(package_format=package_format)
    if search:
        queryset = queryset.filter(
            Q(original_file_name__icontains=search)
            | Q(model__name__icontains=search)
            | Q(brand__name__icontains=search)
            | Q(chipset__icontains=search)
        )
    return queryset


def _paginate_firmware(querysets: dict, page_number):
    """Paginate across firmware tables in the database, newest first."""
    paginator_source: QuerySet[Any] | None = None
    for key, queryset in querysets.items():
        keyed = (
            queryset.order_by()
            .annotate(fw_type=Value(key, output_field=CharField()))
            .values_list("id", "created_at", "fw_type")
        )
        paginator_source = (
            keyed
            if paginator_source is None
            else paginator_source.union(keyed, all=True)
        )
    if paginator_source is None:
        raise ValueError("At least one firmware queryset is required.")
    paginator = Paginator(
        paginator_source.order_by("-created_at", "id"), BROWSE_PAGE_SIZE
    )
    page = paginator.get_page(page_number)
    rows = list(page.object_list)
    wanted: dict[str, list] = defaultdict(list)
    for firmware_id, _created, key in rows:
        wanted[key].append(firmware_id)
    objects = {}
    for key, ids in wanted.items():
        queryset = (
            FIRMWARE_TYPES[key]
            .objects.filter(pk__in=ids)
            .select_related("brand", "model", "variant")
        )
        for firmware in queryset:
            objects[(key, firmware.pk)] = label_firmware(firmware, key)
    page.object_list = [
        objects[(key, firmware_id)]
        for firmware_id, _created, key in rows
        if (key, firmware_id) in objects
    ]
    return page


@require_GET
def firmware_browse(request: HttpRequest) -> HttpResponse:
    """Main firmware browsing page with filters."""
    brand_slug = request.GET.get("brand")
    firmware_type = request.GET.get("type", "all")
    chipset = request.GET.get("chipset")
    search = request.GET.get("q", "").strip()
    package_format = request.GET.get("package_format", "")
    if package_format not in dict(FirmwarePackageFormat.choices):
        package_format = ""

    filters = {
        "brand_slug": brand_slug,
        "chipset": chipset,
        "package_format": package_format,
        "search": search,
    }
    if firmware_type == "all" or not firmware_type:
        type_keys = [key for key, _cls in PUBLIC_FIRMWARE_TYPES]
    else:
        model_class = get_firmware_model(firmware_type)
        if not model_class:
            raise Http404("Invalid firmware type")
        type_keys = [catalog.TYPE_KEY_BY_CLASS[model_class]]
    querysets = {
        key: _filtered_firmware_queryset(FIRMWARE_TYPES[key], **filters)
        for key in type_keys
    }
    firmwares = _paginate_firmware(querysets, request.GET.get("page", 1))

    chipsets = (
        Chipset.objects.annotate(model_total=Count("models", distinct=True))
        .filter(model_total__gt=0)
        .order_by("vendor", "name")
    )
    context = {
        "firmwares": firmwares,
        "brands": Brand.objects.all().order_by("name"),
        "chipsets": chipsets,
        "selected_brand": brand_slug,
        "selected_type": firmware_type,
        "selected_chipset": chipset,
        "selected_package_format": package_format,
        "search_query": search,
        "firmware_types": [
            ("all", "All Types"),
            *[
                (value, FIRMWARE_TYPE_DISPLAY[value])
                for value, _ in PUBLIC_FIRMWARE_TYPES
            ],
        ],
        "package_formats": [
            ("", "All formats"),
            *FirmwarePackageFormat.choices,
        ],
    }
    if request.headers.get("HX-Request"):
        return render(request, "firmwares/fragments/firmware_table.html", context)
    return render(request, "firmwares/browse.html", context)


@require_GET
def brand_list(request: HttpRequest) -> HttpResponse:
    """List all brands grouped by alphabet."""
    brands = list(
        Brand.objects.annotate(
            model_count=Count("models", filter=Q(models__is_active=True))
        ).order_by("name")
    )
    firmware_counts = public_counts("brand_id")
    total_firmwares = 0
    total_models = 0
    brands_by_letter: dict[str, list] = defaultdict(list)
    for brand in brands:
        brand.firmware_count = firmware_counts.get(brand.pk, 0)
        total_firmwares += brand.firmware_count
        total_models += brand.model_count
        letter = brand.name[:1].upper() if brand.name else "#"
        brands_by_letter[letter if letter.isalpha() else "#"].append(brand)

    letters = sorted(letter for letter in brands_by_letter if letter != "#")
    if "#" in brands_by_letter:
        letters.append("#")
    context = {
        "brands": brands,
        "grouped_brands": [(letter, brands_by_letter[letter]) for letter in letters],
        "alphabet": letters,
        "total_firmwares": total_firmwares,
        "total_models": total_models,
    }
    return render(request, "firmwares/brand_list.html", context)


@require_GET
def brand_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Brand page: logo, categories, chipsets, tools, guides, blog links, models."""
    brand = get_object_or_404(Brand, slug=slug)
    models_qs = (
        Model.objects.filter(brand=brand, is_active=True)
        .select_related("chipset_ref")
        .order_by("name")
    )
    paginator = Paginator(models_qs, BRAND_MODEL_PAGE_SIZE)
    models_page = paginator.get_page(request.GET.get("page"))
    model_counts = public_counts("model_id", brand=brand)
    for model in models_page:
        model.firmware_count = model_counts.get(model.pk, 0)

    brand_filter = Q(models__brand=brand, models__is_active=True)
    chipsets = list(
        Chipset.objects.filter(brand_filter)
        .annotate(model_total=Count("models", filter=brand_filter, distinct=True))
        .order_by("vendor", "name")[:24]
    )
    categories = _category_summary(brand=brand)
    context = {
        "brand": brand,
        "logo_src": brand.logo_url if brand.logo_url.startswith("/") else "",
        "models": models_page,
        "model_total": paginator.count,
        "total_firmwares": sum(count for _k, _l, count in categories),
        "categories": categories,
        "chipsets": chipsets,
        "tools": _tools_for(brand, chipsets),
        "guides": _guides(brand=brand)[:6],
        "brand_post": _automated_post("brand", brand),
        "blog_posts": _published_posts(firmware_brand=brand).order_by("-published_at")[
            :6
        ],
        "other_brands": Brand.objects.exclude(pk=brand.pk).order_by("name")[:12],
    }
    return render(request, "firmwares/brand_detail.html", context)


@require_GET
def chipset_list(request: HttpRequest) -> HttpResponse:
    """All chipsets with the number of device models that use them."""
    query = request.GET.get("q", "").strip()
    chipsets = Chipset.objects.annotate(
        model_total=Count("models", filter=Q(models__is_active=True), distinct=True)
    ).order_by("vendor", "name")
    if query:
        chipsets = chipsets.filter(
            Q(name__icontains=query)
            | Q(vendor__icontains=query)
            | Q(family__icontains=query)
        )
    paginator = Paginator(chipsets, CHIPSET_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    context = {
        "chipsets": page,
        "total_chipsets": paginator.count,
        "search_query": query,
        "pagination_query": f"q={quote(query)}&" if query else "",
        "vendors": list(
            Chipset.objects.order_by("vendor")
            .values_list("vendor", flat=True)
            .distinct()
        ),
    }
    return render(request, "firmwares/chipset_list.html", context)


@require_GET
def chipset_detail(request: HttpRequest, slug: str) -> HttpResponse:
    """Chipset page: devices using it, firmware categories, tools and guides."""
    chipset = get_object_or_404(Chipset, slug=slug)
    models_qs = (
        Model.objects.filter(
            Q(chipset_ref=chipset) | Q(variants__chipset_ref=chipset), is_active=True
        )
        .select_related("brand")
        .distinct()
        .order_by("brand__name", "name")
    )
    paginator = Paginator(models_qs, BRAND_MODEL_PAGE_SIZE)
    models_page = paginator.get_page(request.GET.get("page"))
    model_counts = public_counts("model_id", catalog.chipset_filter(chipset))
    for model in models_page:
        model.firmware_count = model_counts.get(model.pk, 0)

    chipset_filter = catalog.chipset_filter(chipset)
    categories = _category_summary(chipset_filter)
    context = {
        "chipset": chipset,
        "models": models_page,
        "model_total": paginator.count,
        "categories": categories,
        "total_firmwares": sum(count for _k, _l, count in categories),
        "tools": _tools_for(None, [chipset]),
        "guides": _guides(model__in=[m.pk for m in models_page])[:6],
        "chipset_post": _automated_post("chipset", chipset),
        "brands": Brand.objects.filter(
            Q(models__chipset_ref=chipset) | Q(models__variants__chipset_ref=chipset)
        )
        .distinct()
        .order_by("name"),
        "related_chipsets": Chipset.objects.filter(vendor=chipset.vendor)
        .exclude(pk=chipset.pk)
        .order_by("name")[:12],
    }
    return render(request, "firmwares/chipset_detail.html", context)


def _model_specifications(model: Model) -> list[tuple[str, str]]:
    def join(values) -> str:
        return ", ".join(str(value) for value in values) if values else ""

    return [
        ("Marketing name", model.marketing_name),
        ("Also known as", model.also_known_as),
        ("Model codes", model.all_model_codes_display),
        ("CPU", model.cpu),
        ("GPU", model.gpu),
        ("Network", model.network_technology),
        ("RAM options", join(model.ram_options)),
        ("Storage options", join(model.storage_options)),
        ("Colors", join(model.colors)),
        ("Dimensions", model.dimensions),
        ("Weight", model.weight),
    ]


@require_GET
def model_detail(
    request: HttpRequest, brand_slug: str, model_slug: str
) -> HttpResponse:
    """Model detail page with all firmwares."""
    brand = get_object_or_404(Brand, slug=brand_slug)
    model = get_object_or_404(
        Model.objects.select_related("chipset_ref"), brand=brand, slug=model_slug
    )
    variants = (
        Variant.objects.filter(model=model, is_active=True)
        .select_related("chipset_ref")
        .order_by("name")
    )
    firmwares = get_all_firmwares_for_model(model)
    for firmware in firmwares:
        firmware.file_size_display = catalog.format_file_size(firmware.file_size)

    chipsets = {model.chipset_ref_id: model.chipset_ref} if model.chipset_ref_id else {}
    for variant in variants:
        if variant.chipset_ref_id:
            chipsets.setdefault(variant.chipset_ref_id, variant.chipset_ref)
    chipset_list_ = list(chipsets.values())

    context = {
        "brand": brand,
        "model": model,
        "variants": variants,
        "firmwares": firmwares,
        "firmware_groups": group_by_category(firmwares),
        "total_firmwares": len(firmwares),
        "firmware_types": sorted({fw.firmware_type for fw in firmwares}),
        "model_specifications": _model_specifications(model),
        "chipsets": chipset_list_,
        "tools": _tools_for(brand, chipset_list_, limit=6),
        "guides": _guides(model=model)[:4],
        "model_post": _automated_post("model", model),
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
        get_public_firmware_queryset(model_class).select_related(
            "brand", "model", "variant", "uploader"
        ),
        id=firmware_id,
    )
    model_class.objects.filter(id=firmware_id).update(view_count=F("view_count") + 1)
    FirmwareView.log_view(
        firmware,
        user=request.user if request.user.is_authenticated else None,
        session_key=request.session.session_key or "",
        request=request,
    )

    related_firmwares = []
    related_blog_posts = []
    if firmware.model:
        related_firmwares = [
            f
            for f in get_all_firmwares_for_model(firmware.model)
            if str(f.id) != str(firmware_id)
        ][:8]
        related_blog_posts = list(
            _published_posts(firmware_model=firmware.model).order_by("-created_at")[:4]
        )

    context = {
        "firmware": firmware,
        "firmware_type": firmware_type,
        "firmware_type_display": FIRMWARE_TYPE_DISPLAY.get(
            firmware_type, firmware_type
        ),
        "related_firmwares": related_firmwares,
        "related_blog_posts": related_blog_posts,
        "file_size_display": catalog.format_file_size(firmware.file_size),
        "external_host": _external_host(firmware),
        "access": check_download_access(request.user, firmware),
        "changelog": (firmware.metadata or {}).get("changelog", ""),
        "login_url": f"{reverse('users:account_login')}?next={quote(request.path)}",
    }
    return render(request, "firmwares/firmware_detail.html", context)


# ============================================================
# Download Views
# ============================================================


def _download_redirect(firmware_type: str, firmware_id) -> HttpResponse:
    return redirect(
        "firmwares:firmware_download",
        firmware_type=firmware_type,
        firmware_id=firmware_id,
    )


def resolve_stored_path(firmware) -> Path | None:
    """Absolute path of a stored firmware file, confined to FIRMWARE_STORAGE_ROOT."""
    if firmware.is_external or not firmware.stored_file_path:
        return None
    root = storage_root().resolve()
    candidate = Path(firmware.stored_file_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    return resolved


def _file_available(firmware) -> bool:
    if firmware.is_external:
        try:
            validate_external_url(firmware.external_url)
        except ValidationError:
            return False
        return True
    return resolve_stored_path(firmware) is not None


@require_GET
def firmware_download(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Firmware download page: server-side countdown and optional ad gate."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        raise Http404("Invalid firmware type")

    firmware = get_object_or_404(
        get_public_firmware_queryset(model_class).select_related(
            "brand", "model", "variant"
        ),
        id=firmware_id,
    )

    if check_hotlink(request):
        raise Http404("Direct linking is not allowed.")

    config = _get_download_config()
    allowed, reason = check_download_allowed(request, firmware)
    if not allowed:
        messages.error(request, reason)
        return redirect(
            "firmwares:firmware_detail",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    token = create_download_token(firmware, request)
    remaining = countdown_remaining(token)
    ad_required = token.ad_gate_required and not token.ad_gate_completed
    ad_remaining = seconds_until(token, config["ad_gate_seconds"]) if ad_required else 0
    can_start = remaining == 0 and not ad_required

    context = {
        "firmware": firmware,
        "firmware_type": firmware_type,
        "firmware_type_display": FIRMWARE_TYPE_DISPLAY.get(
            firmware_type, firmware_type
        ),
        "file_size_display": catalog.format_file_size(firmware.file_size),
        "download_token": token.token,
        "download_signature": sign_token(token.token),
        "countdown_seconds": config["countdown_seconds"],
        "remaining_seconds": remaining,
        "ad_required": ad_required,
        "ad_remaining_seconds": ad_remaining,
        "ad_gate_seconds": config["ad_gate_seconds"],
        "can_start": can_start,
        "refresh_seconds": max(remaining, ad_remaining),
        "external_host": _external_host(firmware),
        "gate_enabled": config["gate_enabled"],
    }
    return render(request, "firmwares/download.html", context)


@require_POST
def firmware_download_start(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Consume the single-use token and send the browser to the file."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        raise Http404("Invalid firmware type")

    firmware = get_object_or_404(
        get_public_firmware_queryset(model_class), id=firmware_id
    )

    token_str = request.POST.get("token", "")
    sig = request.POST.get("sig", "")
    if not token_str or not sig:
        messages.error(request, "Invalid download link. Please try again.")
        return _download_redirect(firmware_type, firmware_id)
    if not verify_signature(token_str, sig):
        messages.error(request, "Download link signature is invalid.")
        return _download_redirect(firmware_type, firmware_id)

    if not _file_available(firmware):
        messages.error(request, "File not available for download.")
        return redirect(
            "firmwares:firmware_detail",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    decision = check_download_access(request.user, firmware)
    if not decision.allowed:
        messages.error(request, decision.reason)
        return redirect(
            "firmwares:firmware_detail",
            firmware_type=firmware_type,
            firmware_id=firmware_id,
        )

    try:
        token = consume_download_token(token_str, firmware, request)
    except TokenError as error:
        if error.code == "countdown":
            messages.error(
                request,
                f"Please wait {error.wait_seconds} more seconds before starting.",
            )
        elif error.code == "adgate":
            messages.error(request, "Please complete the ad gate first.")
        elif error.code == "invalid":
            messages.error(request, "Invalid download token for this firmware.")
        else:
            messages.error(request, "Download link has expired. Please try again.")
        return _download_redirect(firmware_type, firmware_id)

    model_class.objects.filter(id=firmware_id).update(
        download_count=F("download_count") + 1
    )
    logger.info(
        "Firmware download: %s by %s (token=%s)",
        firmware.original_file_name,
        request.user if request.user.is_authenticated else "anonymous",
        token_str[:10],
    )

    if firmware.is_external:
        if not firmware.external_url:
            raise Http404("Firmware has no external download link")
        finalize_served_download(request.user, firmware)
        return redirect(firmware.external_url)
    signed_url = generate_signed_url(firmware, token)
    if signed_url is None:
        raise Http404("File not available for download")
    return redirect(signed_url)


@require_POST
def firmware_ad_gate_complete(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> HttpResponse:
    """Mark the ad gate complete for a token (JSON for XHR, redirect for forms)."""
    wants_json = "application/json" in request.headers.get("Accept", "") or bool(
        request.headers.get("HX-Request")
    )
    token_str = request.POST.get("token", "")
    sig = request.POST.get("sig", "")

    def fail(message: str, status: int = 400) -> HttpResponse:
        if wants_json:
            return JsonResponse({"ok": False, "error": message}, status=status)
        messages.error(request, message)
        return _download_redirect(firmware_type, firmware_id)

    if not token_str or not sig or not verify_signature(token_str, sig):
        return fail("Invalid token")
    token = validate_download_token(token_str)
    if not token:
        return fail("Token expired")

    ad_type = request.POST.get("ad_type", "video")
    if ad_type not in ("video", "banner", "interstitial"):
        ad_type = "video"
    if not complete_ad_gate(token, ad_type=ad_type):
        return fail("The ad gate has not run for long enough yet.")
    if wants_json:
        return JsonResponse({"ok": True})
    return _download_redirect(firmware_type, firmware_id)


@require_GET
def serve_local_file(request: HttpRequest, firmware_id: str) -> HttpResponseBase:
    """Serve a stored firmware file through a short-lived signed link.

    The link is bound to the token and firmware, expires after
    ``FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS`` and is only valid for a token that the
    download page already consumed (countdown and ad gate passed).
    """
    token_str = request.GET.get("token", "")
    if not verify_link(
        token_str,
        str(firmware_id),
        request.GET.get("exp", ""),
        request.GET.get("sig", ""),
    ):
        raise Http404

    token = DownloadToken.objects.filter(
        token=token_str, status=DownloadToken.Status.USED
    ).first()
    if token is None or token.object_id != firmware_id:
        raise Http404
    firmware = token.firmware
    if firmware is None or not (firmware.is_active and firmware.is_verified):
        raise Http404

    path = resolve_stored_path(firmware)
    if path is None:
        logger.error("Stored firmware file missing or outside storage: %s", firmware_id)
        raise Http404

    decision = check_download_access(token.user, firmware)
    if not decision.allowed:
        raise PermissionDenied(decision.reason)
    finalize_served_download(token.user, firmware)

    session = token.sessions.order_by("-started_at").first()
    if session is not None:
        session.status = DownloadSession.Status.COMPLETED
        session.bytes_delivered = path.stat().st_size
        session.completed_at = timezone.now()
        session.save(update_fields=["status", "bytes_delivered", "completed_at"])
    FirmwareDownloadAttempt.objects.filter(
        content_type=token.content_type,
        object_id=token.object_id,
        user=token.user,
        status="ready",
    ).update(status="downloading", bytes_transferred=path.stat().st_size)

    filename = firmware.original_file_name or path.name
    if not getattr(settings, "DEBUG", True):
        prefix = getattr(settings, "FIRMWARE_X_ACCEL_PREFIX", "/protected-firmware/")
        relative = path.relative_to(storage_root().resolve())
        response = HttpResponse()
        response["X-Accel-Redirect"] = f"{prefix}{relative.as_posix()}"
        response["Content-Type"] = "application/octet-stream"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)


# ============================================================
# API Endpoints for AJAX
# ============================================================


@require_GET
def api_firmware_stats(
    request: HttpRequest, firmware_type: str, firmware_id: str
) -> JsonResponse:
    """Get firmware stats (views, downloads) for publicly listed firmware."""
    model_class = get_firmware_model(firmware_type)
    if not model_class:
        return JsonResponse({"error": "Invalid firmware type"}, status=404)

    firmware = get_public_firmware_queryset(model_class).filter(id=firmware_id).first()
    if firmware is None:
        return JsonResponse({"error": "Firmware not found"}, status=404)
    return JsonResponse(
        {"views": firmware.view_count, "downloads": firmware.download_count}
    )


@require_GET
def api_search_autocomplete(request: HttpRequest) -> JsonResponse:
    """Global search autocomplete: brands, models, variants, chipsets, firmware, blog."""
    query = request.GET.get("q", "").strip()
    search_type = request.GET.get("type", "all")
    try:
        limit = max(1, min(int(request.GET.get("limit", 10)), 20))
    except ValueError:
        limit = 10

    if len(query) < 2:
        return JsonResponse({"results": [], "query": query})

    results = []

    if search_type in ("all", "brands"):
        brands = (
            Brand.objects.filter(Q(name__icontains=query) | Q(slug__icontains=query))
            .annotate(model_total=Count("models"))
            .order_by("name")[:limit]
        )
        for brand in brands:
            results.append(
                {
                    "type": "brand",
                    "id": brand.id,
                    "name": brand.name,
                    "slug": brand.slug,
                    "url": reverse("firmwares:brand_detail", args=[brand.slug]),
                    "icon": "building",
                    "subtitle": f"{brand.model_total} model{'s' if brand.model_total != 1 else ''}",
                }
            )

    if search_type in ("all", "chipsets"):
        chipsets = Chipset.objects.filter(
            Q(name__icontains=query)
            | Q(vendor__icontains=query)
            | Q(family__icontains=query)
        ).order_by("vendor", "name")[:limit]
        for chipset in chipsets:
            results.append(
                {
                    "type": "chipset",
                    "id": chipset.id,
                    "name": chipset.display_name,
                    "slug": chipset.slug,
                    "url": reverse("firmwares:chipset_detail", args=[chipset.slug]),
                    "icon": "cpu",
                    "subtitle": chipset.family or chipset.vendor,
                }
            )

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
            subtitle_parts = [model.brand.name]
            if model.model_code:
                subtitle_parts.append(model.model_code)
            results.append(
                {
                    "type": "model",
                    "id": model.id,
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

    if search_type in ("all", "variants"):
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
            subtitle_parts = [f"{variant.model.brand.name} {variant.model.name}"]
            if variant.chipset:
                subtitle_parts.append(variant.chipset)
            if variant.region:
                subtitle_parts.append(variant.region)
            results.append(
                {
                    "type": "variant",
                    "id": variant.id,
                    "name": variant.name,
                    "slug": variant.slug,
                    "url": reverse(
                        "firmwares:model_detail",
                        args=[variant.model.brand.slug, variant.model.slug],
                    ),
                    "icon": "layers",
                    "subtitle": " • ".join(subtitle_parts),
                    "chipset": variant.chipset or "",
                    "region": variant.region or "",
                }
            )

    if search_type in ("all", "firmwares"):
        remaining = limit - len(results) if search_type == "all" else limit
        for type_key, model_class in PUBLIC_FIRMWARE_TYPES:
            if remaining <= 0:
                break
            firmwares = (
                get_public_firmware_queryset(model_class)
                .filter(
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
                    | Q(variant__chipset__icontains=query)
                )
                .select_related("brand", "model", "variant")[:remaining]
            )
            for firmware in firmwares:
                subtitle_parts = [
                    part
                    for part in (
                        firmware.brand.name if firmware.brand else "",
                        firmware.model.name if firmware.model else "",
                        firmware.variant.name if firmware.variant else "",
                        firmware.chipset,
                    )
                    if part
                ]
                name = firmware.original_file_name
                results.append(
                    {
                        "type": "firmware",
                        "id": str(firmware.id),
                        "name": name[:60] + ("..." if len(name) > 60 else ""),
                        "url": reverse(
                            "firmwares:firmware_detail", args=[type_key, firmware.id]
                        ),
                        "icon": "file-archive",
                        "subtitle": " • ".join(subtitle_parts[:3]) or type_key.title(),
                        "firmware_type": type_key,
                        "chipset": firmware.chipset or "",
                        "android_version": firmware.android_version or "",
                    }
                )
                remaining -= 1

    if search_type in ("all", "blog"):
        remaining = limit - len(results) if search_type == "all" else limit
        if remaining > 0:
            posts = (
                _published_posts()
                .filter(
                    Q(title__icontains=query)
                    | Q(summary__icontains=query)
                    | Q(category__name__icontains=query)
                    | Q(tags__name__icontains=query)
                )
                .select_related("category")
                .prefetch_related("tags")
                .distinct()
                .order_by("-published_at")[:remaining]
            )
            for post in posts:
                tag_names = [
                    tag.name
                    for tag in post.tags.all()
                    if tag.is_curated and tag.is_active and not tag.is_deleted
                ][:3]
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
                        "id": post.id,
                        "name": post.title,
                        "slug": post.slug,
                        "url": reverse("blog:post_detail", args=[post.slug]),
                        "icon": "file-text",
                        "subtitle": " • ".join(subtitle_parts) or "Blog Post",
                    }
                )

    return JsonResponse(
        {"results": results[:limit], "query": query, "count": len(results)}
    )
