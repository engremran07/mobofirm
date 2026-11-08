from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from apps.firmwares.public_views import library_hub
from apps.users.views import password_reset_entry

from . import health, views

urlpatterns = [
    path("", library_hub, name="home"),
    path("admin/", admin.site.urls),
    path(
        "accounts/profile/",
        RedirectView.as_view(pattern_name="users:profile", permanent=False),
        name="account_profile_compat",
    ),
    # Stock allauth entry points skip the app's rate limit, reCAPTCHA, device
    # policy and MFA handling, so they are redirected or closed.
    path(
        "accounts/login/",
        RedirectView.as_view(pattern_name="users:account_login", query_string=True),
    ),
    path(
        "accounts/signup/",
        RedirectView.as_view(pattern_name="users:account_signup", query_string=True),
    ),
    path("accounts/google/login/token/", views.disabled_endpoint),
    path("accounts/password/reset/", password_reset_entry),
    path("accounts/", include("allauth.urls")),
    path("health/live/", health.liveness, name="health-live"),
    path("health/ready/", health.readiness, name="health-ready"),
    path("search/", include("apps.core.search.urls")),
    path("users/", include("apps.users.urls")),
    path("devices/", include("apps.devices.urls")),
    path("firmwares/", include("apps.firmwares.urls")),
    path("tags/", include("apps.tags.urls")),
    path("ads/", include("apps.ads.urls")),
    path("blog/", include("apps.blog.urls")),
    path("comments/", include("apps.comments.urls")),
    path("", include("apps.seo.urls_root")),
    path("seo/", include("apps.seo.urls")),
    path("pages/", include("apps.pages.urls")),
    path("storage/", include("apps.storage.urls")),
    path("shop/", include("apps.shop.urls")),
    path("marketplace/", include("apps.marketplace.urls")),
    path("wallet/", include("apps.wallet.urls")),
    path("referral/", include("apps.referral.urls")),
    path("offers/", include("apps.promotions.urls")),
    path("files/", include("apps.users.urls_library")),
    path("consent/", include("apps.consent.urls")),
    path("", include("apps.gamification.urls")),
    path("bounty/", include("apps.bounty.urls")),
    path("go/", include("apps.distribution.urls")),
    path("site/", include("apps.site_settings.urls")),
    # Last: its catch-all verification-file pattern must never shadow another route.
    path("", include("apps.site_settings.urls_root")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler400 = "apps.core.views.error_400_view"
handler403 = "apps.core.views.error_403_view"
handler404 = "apps.core.views.error_404_view"
handler500 = "apps.core.views.error_500_view"
