"""Root-level utility routes: /sitemap.xml, /sitemap-<section>.xml, /robots.txt and
the IndexNow ownership key file."""

from django.urls import path

from . import sitemap_views

app_name = "seo_public"

urlpatterns = [
    path("robots.txt", sitemap_views.robots_txt, name="robots"),
    path("sitemap.xml", sitemap_views.sitemap_index, name="sitemap"),
    path(
        "sitemap-<slug:section>.xml",
        sitemap_views.sitemap_section,
        name="sitemap_section",
    ),
    path("<slug:key>.txt", sitemap_views.indexnow_key, name="indexnow_key"),
]
