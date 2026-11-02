from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .api import (
    ChangelogEntryViewSet,
    DownloadTokenViewSet,
    FirmwareDiffViewSet,
    GSMArenaDeviceViewSet,
    HotlinkBlockViewSet,
    TrustedTesterViewSet,
    VerificationReportViewSet,
)
from .autofill_views import (
    autofill_brand,
    autofill_model,
    autofill_variant,
)
from .public_views import (
    api_firmware_stats,
    api_search_autocomplete,
    brand_detail,
    brand_list,
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
from .views import (
    BrandCreationRequestViewSet,
    FirmwareUploadView,
    ModelCreationRequestViewSet,
    ModerationView,
    PendingFirmwareViewSet,
    SchemaUpdateProposalViewSet,
    VariantCreationRequestViewSet,
)
from .views_flashing import flashing_tool_detail, flashing_tools

app_name = "firmwares"

router = DefaultRouter()
router.register("pending", PendingFirmwareViewSet, basename="pending-firmware")
router.register(
    "schema-proposals", SchemaUpdateProposalViewSet, basename="schema-proposals"
)
router.register(
    "brand-requests", BrandCreationRequestViewSet, basename="brand-requests"
)
router.register(
    "model-requests", ModelCreationRequestViewSet, basename="model-requests"
)
router.register(
    "variant-requests", VariantCreationRequestViewSet, basename="variant-requests"
)
# Merged from dissolved apps
router.register("download-tokens", DownloadTokenViewSet, basename="download-tokens")
router.register("hotlink-blocks", HotlinkBlockViewSet, basename="hotlink-blocks")
router.register(
    "verification-reports", VerificationReportViewSet, basename="verification-reports"
)
router.register("trusted-testers", TrustedTesterViewSet, basename="trusted-testers")
router.register("gsmarena-devices", GSMArenaDeviceViewSet, basename="gsmarena-devices")
# Merged from apps.changelog
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
    # Flashing tools catalog
    path("tools/", flashing_tools, name="flashing_tools"),
    path("tools/<slug:slug>/", flashing_tool_detail, name="flashing_tool_detail"),
    # Admin/upload endpoints
    path("upload/", FirmwareUploadView.as_view(), name="firmware-upload"),
    path("moderate/<uuid:pk>/", ModerationView.as_view(), name="firmware-moderate"),
    # Auto-fill endpoints (autofill/ prefix avoids brand slug collision)
    path("autofill/brand/<int:brand_id>/", autofill_brand, name="autofill-brand"),
    path("autofill/model/<int:model_id>/", autofill_model, name="autofill-model"),
    path(
        "autofill/variant/<int:variant_id>/",
        autofill_variant,
        name="autofill-variant",
    ),
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
    # FINDING-0019: Serve locally stored firmware files via HMAC-signed URL.
    # URL: /firmwares/serve/<uuid:firmware_id>/?token=<tok>&sig=<hmac>
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
