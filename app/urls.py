from django.contrib import admin
from django.urls import include, path

from . import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/live/", health.liveness, name="health-live"),
    path("health/ready/", health.readiness, name="health-ready"),
    path("governance/", include("apps.governance.urls")),
]
