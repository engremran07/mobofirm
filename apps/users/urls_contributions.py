"""URLs for "My contributions": the hub, its contribution tabs, downloads and quota."""

from django.urls import path

from . import library_views

app_name = "contributions"

urlpatterns = [
    path("", library_views.hub_view, name="hub"),
    path("firmware/", library_views.uploads_view, name="firmware"),
    path("blog/", library_views.blog_view, name="blog"),
    path("downloads/", library_views.library_view, name="downloads"),
    path("quota/", library_views.quota_view, name="quota"),
]
