from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.firmwares.public_views import library_hub

from . import health

urlpatterns = [
    path("", library_hub, name="home"),
    path("admin/", admin.site.urls),
    path(
        "accounts/profile/",
        RedirectView.as_view(pattern_name="users:profile", permanent=False),
        name="account_profile_compat",
    ),
    path("accounts/", include("allauth.urls")),
    path("health/live/", health.liveness, name="health-live"),
    path("health/ready/", health.readiness, name="health-ready"),
    path("users/", include("apps.users.urls")),
    path("devices/", include("apps.devices.urls")),
    path("firmwares/", include("apps.firmwares.urls")),
    path("tags/", include("apps.tags.urls")),
    path("ads/", include("apps.ads.urls")),
    path("blog/", include("apps.blog.urls")),
    path("comments/", include("apps.comments.urls")),
    path("governance/", include("apps.governance.urls")),
]
