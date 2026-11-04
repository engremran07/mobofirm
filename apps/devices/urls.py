from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views
from .api import BehaviorInsightViewSet, DeviceFingerprintViewSet

app_name = "devices"

router = DefaultRouter()
router.register(
    "fingerprints", DeviceFingerprintViewSet, basename="device-fingerprints"
)
router.register("behavior", BehaviorInsightViewSet, basename="behavior-insights")

urlpatterns = [
    # Device-specific views only (browsing moved to firmwares app)
    path("my/", views.my_devices, name="my_devices"),
    path("events/", views.device_events, name="device_events"),
    path("payload/", views.device_payload_view, name="device_payload"),
    path("acknowledge/", views.acknowledge_new_device, name="acknowledge_new_device"),
    *router.urls,
]
