"""URLs for the legacy "My files" addresses: permanent redirects to My contributions.

The names stay reversible (``library:my_files``, ``library:my_uploads`` and
``library:my_quota``) so existing links, bookmarks and templates keep working.
"""

from django.urls import path

from . import library_views

app_name = "library"

urlpatterns = [
    path("", library_views.legacy_my_files, name="my_files"),
    path("uploads/", library_views.legacy_my_uploads, name="my_uploads"),
    path("quota/", library_views.legacy_my_quota, name="my_quota"),
]
