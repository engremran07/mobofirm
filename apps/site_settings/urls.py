from __future__ import annotations

from django.urls import path

from . import editor_views, views

app_name = "site_settings"

urlpatterns = [
    path("editor/upload/", editor_views.upload, name="editor_upload"),
    path("info/", views.site_info, name="info"),
    path("theme.css", views.theme_css, name="theme_css"),
    path(
        "verification/<str:filename>/",
        views.verification_file,
        name="verification_file",
    ),
]
