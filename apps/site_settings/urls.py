from __future__ import annotations

from django.urls import path

from . import views

app_name = "site_settings"

urlpatterns = [
    path("info/", views.site_info, name="info"),
    path("theme.css", views.theme_css, name="theme_css"),
    path(
        "verification/<str:filename>/",
        views.verification_file,
        name="verification_file",
    ),
]
