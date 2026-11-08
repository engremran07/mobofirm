"""URLs for "My files": the library, the user's uploads and their download quota."""

from django.urls import path

from . import library_views

app_name = "library"

urlpatterns = [
    path("", library_views.library_view, name="my_files"),
    path("uploads/", library_views.uploads_view, name="my_uploads"),
    path("quota/", library_views.quota_view, name="my_quota"),
]
