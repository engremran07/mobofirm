from django.urls import path

from . import feeds, views, views_api, views_manage

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="post_list"),
    path("manage/", views_manage.manage_posts, name="manage_posts"),
    path("manage/bulk_publish/", views_manage.bulk_publish, name="bulk_publish"),
    path("create/", views_manage.post_create, name="post_create"),
    path(
        "manage/<slug:slug>/workflow/",
        views_manage.post_workflow,
        name="post_workflow",
    ),
    path(
        "manage/<slug:slug>/history/",
        views_manage.post_history,
        name="post_history",
    ),
    path(
        "manage/<slug:slug>/history/<int:revision_id>/restore/",
        views_manage.restore_post_revision,
        name="restore_post_revision",
    ),
    path("category/create/", views_manage.category_create, name="category_create"),
    path("archives/<int:year>/", views.post_archive_year, name="post_archive_year"),
    path(
        "archives/<int:year>/<int:month>/",
        views.post_archive_month,
        name="post_archive_month",
    ),
    path("feed/rss/", feeds.LatestRssFeed(), name="feed_rss"),
    path("feed/atom/", feeds.LatestAtomFeed(), name="feed_atom"),
    path("feed/json/", feeds.json_feed, name="feed_json"),
    path("api/ai/assist/", views_api.api_ai_assist, name="api_ai_assist"),
    path("api/similar/", views_api.api_similar_posts, name="api_similar_posts"),
    path("api/workflow/<slug:slug>/", views_api.api_workflow, name="api_workflow"),
    path("api/posts/", views_api.api_posts, name="api_posts"),
    path("api/posts/<slug:slug>/related/", views_api.api_related, name="api_related"),
    path("api/autosave/", views_api.post_autosave, name="post_autosave"),
    path("api/preview/", views_api.post_preview, name="post_preview"),
    path("post/<int:pk>/like/", views_api.post_like, name="post_like"),
    path("post/<int:pk>/bookmark/", views_api.post_bookmark, name="post_bookmark"),
    # Brand / model / chipset hubs (mirror the firmware catalogue hierarchy)
    path("brand/<slug:brand_slug>/", views.brand_posts, name="brand_posts"),
    path(
        "brand/<slug:brand_slug>/<slug:model_slug>/",
        views.model_posts,
        name="model_posts",
    ),
    path("chipset/<slug:slug>/", views.chipset_posts, name="chipset_posts"),
    path("search/", views.blog_search, name="blog_search"),
    # Post detail — MUST be last (catch-all slug)
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
