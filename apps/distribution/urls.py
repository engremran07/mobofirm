from __future__ import annotations

from django.urls import path

from apps.distribution import views

app_name = "distribution"

urlpatterns = [
    path(
        "whatsapp/<int:channel_id>/webhook/",
        views.whatsapp_webhook,
        name="whatsapp_webhook",
    ),
    path("<slug:code>/", views.go, name="go"),
]
