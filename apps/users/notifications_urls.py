from __future__ import annotations

from django.urls import path

from .views_notifications import (
    notification_detail,
    notification_list,
    notification_mark_all_read,
    notification_mark_read,
    notification_unread_count,
)

app_name = "users_notifications"

urlpatterns = [
    # HTML
    path("", notification_list, name="list"),
    path(
        "<int:pk>/", notification_detail, name="detail"
    ),  # ✔ matches view PK type (INT)
    # JSON
    path("count/unread/", notification_unread_count, name="unread_count"),
    # Mutations
    path("mark/<int:pk>/", notification_mark_read, name="mark_read"),
    path("mark-all/", notification_mark_all_read, name="mark_all"),
]
