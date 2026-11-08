from django.urls import path

from . import dialogs, views

app_name = "tags"

urlpatterns = [
    path("search/", views.search, name="search"),
    path("suggest/", views.suggest_tags, name="suggest"),
    path("merge/", views.merge_tags, name="merge"),
    path("merge/dialog/", dialogs.MergeTagsDialog.as_view(), name="merge_dialog"),
    path(
        "review/<int:tag_id>/reject/dialog/",
        dialogs.RejectSuggestionDialog.as_view(),
        name="reject_dialog",
    ),
    path("review/", views.review_tag, name="review"),
    path("manage/", views.manage_tags, name="manage"),
    path("analytics/", views.tag_analytics, name="analytics"),
    path("duplicates/", views.search_duplicates, name="duplicates"),
    path("duplicates/review/", views.duplicates_review, name="duplicates_review"),
    path("keywords/review/", views.keyword_review, name="keyword_review"),
    path("keywords/apply/", views.apply_keyword, name="apply_keyword"),
    path("", views.tag_list, name="list"),
    path("<slug:slug>/", views.tag_detail, name="detail"),
]
