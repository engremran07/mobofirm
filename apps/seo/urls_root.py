"""Root-level utility routes: robots.txt, the sitemap index and its numbered files
(``/sitemap-<section>[-<partition>]-<n>.xml``), the sitemap stylesheet, the
well-known text files and the IndexNow ownership key file."""

from django.urls import path, re_path

from . import sitemap_views

app_name = "seo_public"

urlpatterns = [
    path("robots.txt", sitemap_views.robots_txt, name="robots"),
    path("site-map/", sitemap_views.human_sitemap, name="human_sitemap"),
    path("sitemap.xml", sitemap_views.sitemap_index, name="sitemap"),
    path(
        "sitemap.xml.gz",
        sitemap_views.sitemap_index,
        {"gz": ".gz"},
        name="sitemap_gz",
    ),
    path("sitemap-news.xml", sitemap_views.sitemap_news, name="sitemap_news"),
    path(
        "sitemap-news.xml.gz",
        sitemap_views.sitemap_news,
        {"gz": ".gz"},
        name="sitemap_news_gz",
    ),
    re_path(
        r"^sitemap-(?P<key>[a-z0-9_]+(?:-[a-z0-9_]+)*)-(?P<page>[1-9][0-9]*)"
        r"\.xml(?P<gz>\.gz)?$",
        sitemap_views.sitemap_file,
        name="sitemap_file",
    ),
    path(
        "sitemap-<slug:section>.xml",
        sitemap_views.sitemap_legacy,
        name="sitemap_section",
    ),
    path(".well-known/security.txt", sitemap_views.security_txt, name="security_txt"),
    path("humans.txt", sitemap_views.humans_txt, name="humans"),
    path("ads.txt", sitemap_views.ads_txt, name="ads"),
    path("app-ads.txt", sitemap_views.app_ads_txt, name="app_ads"),
    path("<slug:key>.txt", sitemap_views.indexnow_key, name="indexnow_key"),
]
