"""Root-level routes: legacy legal paths and search-engine verification files."""

from __future__ import annotations

from django.urls import path, re_path

from . import views

urlpatterns = [
    path(
        "privacy/",
        views.legacy_legal_redirect,
        {"key": "privacy"},
        name="legacy_privacy",
    ),
    path("terms/", views.legacy_legal_redirect, {"key": "terms"}, name="legacy_terms"),
    path(
        "cookies/",
        views.legacy_legal_redirect,
        {"key": "cookies"},
        name="legacy_cookies",
    ),
    # Unnamed on purpose: only allow-listed files are served, and the view answers
    # every other name with the site's normal 404.
    re_path(
        r"^(?P<filename>[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:html|xml|json))$",
        views.verification_file_root,
    ),
]
