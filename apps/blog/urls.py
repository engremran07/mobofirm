from django.urls import path

from . import feeds, views

app_name = "blog"

urlpatterns = [
    path("", views.post_list, name="post_list"),
    path("manage/", views.manage_posts, name="manage_posts"),
    path("manage/bulk_publish/", views.bulk_publish, name="bulk_publish"),
    path("create/", views.post_create, name="post_create"),
    path("category/create/", views.category_create, name="category_create"),
    path("archives/<int:year>/", views.post_archive_year, name="post_archive_year"),
    path(
        "archives/<int:year>/<int:month>/",
        views.post_archive_month,
        name="post_archive_month",
    ),
    path("feed/rss/", feeds.LatestRssFeed(), name="feed_rss"),
    path("feed/atom/", feeds.LatestAtomFeed(), name="feed_atom"),
    path("feed/json/", feeds.json_feed, name="feed_json"),
    path("api/ai/assist/", views.api_ai_assist, name="api_ai_assist"),
    path("api/similar/", views.api_similar_posts, name="api_similar_posts"),
    path("api/workflow/<slug:slug>/", views.api_workflow, name="api_workflow"),
    path("api/posts/", views.api_posts, name="api_posts"),
    path("api/posts/<slug:slug>/related/", views.api_related, name="api_related"),
    path("api/autosave/", views.post_autosave, name="post_autosave"),
    path("api/preview/", views.post_preview, name="post_preview"),
    path(
        "api/widgets/trending-tags/",
        views.widget_trending_tags,
        name="widget_trending_tags",
    ),
    path("api/widgets/latest/", views.widget_latest_posts, name="widget_latest_posts"),
    path("api/widgets/top/", views.widget_top_posts, name="widget_top_posts"),
    path("api/posts/public/", views.posts_api_public, name="posts_api_public"),
    path("post/<int:pk>/like/", views.post_like, name="post_like"),
    path("post/<int:pk>/bookmark/", views.post_bookmark, name="post_bookmark"),
    # Brand/model blog hierarchy (mirrors /firmwares/<brand>/<model>/)
    path("brand/<slug:brand_slug>/", views.brand_posts, name="brand_posts"),
    path(
        "brand/<slug:brand_slug>/<slug:model_slug>/",
        views.model_posts,
        name="model_posts",
    ),
    # Search
    path("search/", views.blog_search, name="blog_search"),
    # Ad gate — rewards-based content unlock
    path("<slug:slug>/ad-gate/", views.ad_gate_view, name="ad_gate_view"),
    path(
        "<slug:slug>/ad-gate/complete/", views.ad_gate_complete, name="ad_gate_complete"
    ),
    # Post detail — MUST be last (catch-all slug)
    path("<slug:slug>/", views.post_detail, name="post_detail"),
]
