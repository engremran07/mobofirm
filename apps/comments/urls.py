from django.urls import path

from . import views

app_name = "comments"

urlpatterns = [
    path("<slug:slug>/add/", views.add_comment, name="add_comment"),
    path("<slug:slug>/add.json", views.add_comment_json, name="add_comment_json"),
    path("<slug:slug>/list.json", views.list_comments, name="list_comments"),
    path("upvote/<int:comment_id>/", views.upvote_comment, name="upvote_comment"),
    path("report/<int:comment_id>/", views.report_comment, name="report_comment"),
    path("edit/<int:comment_id>/", views.edit_comment, name="edit_comment"),
    path("moderation/", views.moderation_queue, name="moderation_queue"),
    path("moderation/action/", views.moderation_action, name="moderation_action"),
]
