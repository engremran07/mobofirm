from __future__ import annotations

from django.urls import path

from apps.distribution import views

app_name = "distribution"

urlpatterns = [
    path("<slug:code>/", views.go, name="go"),
]
