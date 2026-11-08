from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    ChangelogEntryViewSet,
    DownloadTokenViewSet,
    FirmwareDiffViewSet,
    HotlinkBlockViewSet,
    TrustedTesterViewSet,
    VerificationReportViewSet,
)
from .public_views import (
    api_firmware_stats,
    api_search_autocomplete,
    brand_detail,
    brand_list,
    chipset_detail,
    chipset_list,
    firmware_ad_gate_complete,
    firmware_browse,
    firmware_detail,
    firmware_download,
    firmware_download_start,
    library_hub,
    model_detail,
    serve_local_file,
    universal_search,
)
from .user_views import (
    my_requests,
    my_uploads,
    request_brand,
    request_check,
    request_chipset,
    request_firmware,
    request_model,
    request_variant,
    requests_hub,
    upload_page,
)
from .views import (
    BrandCreationRequestViewSet,
    ChipsetCreationRequestViewSet,
    ExternalFirmwareSubmitView,
    FirmwareUploadView,
    ModelCreationRequestViewSet,
    ModerationView,
    PendingFirmwareViewSet,
    VariantCreationRequestViewSet,
)
from .views_flashing import flashing_guides, flashing_tool_detail, flashing_tools

app_name = "firmwares"

router = DefaultRouter()
router.register("pending", PendingFirmwareViewSet, basename="pending-firmware")
router.register(
    "brand-requests", BrandCreationRequestViewSet, basename="brand-requests"
)
router.register(
    "model-requests", ModelCreationRequestViewSet, basename="model-requests"
)
router.register(
    "variant-requests", VariantCreationRequestViewSet, basename="variant-requests"
)
router.register(
    "chipset-requests", ChipsetCreationRequestViewSet, basename="chipset-requests"
)
# Merged from dissolved apps
router.register("download-tokens", DownloadTokenViewSet, basename="download-tokens")
router.register("hotlink-blocks", HotlinkBlockViewSet, basename="hotlink-blocks")
router.register(
    "verification-reports", VerificationReportViewSet, basename="verification-reports"
)
router.register("trusted-testers", TrustedTesterViewSet, basename="trusted-testers")
router.register(
    "changelog-entries", ChangelogEntryViewSet, basename="changelog-entries"
)
router.register("firmware-diffs", FirmwareDiffViewSet, basename="firmware-diffs")

urlpatterns = [
    # ── Fixed-prefix routes (must come BEFORE dynamic slug catch-alls) ──
    path("", library_hub, name="library_hub"),
    path("search/", universal_search, name="universal_search"),
    path("browse/", firmware_browse, name="browse"),
    path("brands/", brand_list, name="brand_list"),
    # Chipset catalogue
    path("chipsets/", chipset_list, name="chipset_list"),
    path("chipsets/<slug:slug>/", chipset_detail, name="chipset_detail"),
    # Flashing tools and guides hub
    path("tools/", flashing_tools, name="flashing_tools"),
    path("tools/<slug:slug>/", flashing_tool_detail, name="flashing_tool_detail"),
    path("guides/", flashing_guides, name="flashing_guides"),
    # Admin/upload endpoints
    path("upload/", upload_page, name="upload"),
    path("upload/mine/", my_uploads, name="my_uploads"),
    path("api/upload/", FirmwareUploadView.as_view(), name="firmware-upload"),
    path(
        "api/upload/external/",
        ExternalFirmwareSubmitView.as_view(),
        name="firmware-upload-external",
    ),
    path("moderate/<uuid:pk>/", ModerationView.as_view(), name="firmware-moderate"),
    # End-user catalogue requests and the cascading catalogue pickers
    path("requests/", requests_hub, name="requests_hub"),
    path("requests/mine/", my_requests, name="my_requests"),
    path("requests/brand/", request_brand, name="request_brand"),
    path("requests/model/", request_model, name="request_model"),
    path("requests/variant/", request_variant, name="request_variant"),
    path("requests/firmware/", request_firmware, name="request_firmware"),
    path("requests/chipset/", request_chipset, name="request_chipset"),
    path("requests/check/", request_check, name="request_check"),
    # Firmware detail & download (file/ prefix avoids brand/model slug collision)
    path(
        "file/<str:firmware_type>/<uuid:firmware_id>/",
        firmware_detail,
        name="firmware_detail",
    ),
    path(
        "file/<str:firmware_type>/<uuid:firmware_id>/download/",
        firmware_download,
        name="firmware_download",
    ),
    path(
        "file/<str:firmware_type>/<uuid:firmware_id>/download/start/",
        firmware_download_start,
        name="firmware_download_start",
    ),
    path(
        "file/<str:firmware_type>/<uuid:firmware_id>/download/ad-gate/",
        firmware_ad_gate_complete,
        name="firmware_ad_gate_complete",
    ),
    # Serve locally stored firmware via a short-lived, token-bound signed link.
    # URL: /firmwares/serve/<uuid:firmware_id>/?token=<tok>&exp=<ts>&sig=<hmac>
    path(
        "serve/<uuid:firmware_id>/",
        serve_local_file,
        name="serve_local_file",
    ),
    # API endpoints (api/ prefix)
    path(
        "api/<str:firmware_type>/<uuid:firmware_id>/stats/",
        api_firmware_stats,
        name="api_firmware_stats",
    ),
    path(
        "api/search/autocomplete/",
        api_search_autocomplete,
        name="api_search_autocomplete",
    ),
    path("api/", include(router.urls)),
    # ── Dynamic slug catch-alls (MUST be LAST to avoid matching fixed routes) ──
    path("<slug:slug>/", brand_detail, name="brand_detail"),
    path("<slug:brand_slug>/<slug:model_slug>/", model_detail, name="model_detail"),
]
