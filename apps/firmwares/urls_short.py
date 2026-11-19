"""``/d/<code>/``: first-party short links for external firmware downloads."""

from django.urls import path

from .shortener_views import short_download

app_name = "firmware_short"

urlpatterns = [
    path("<str:code>/", short_download, name="go"),
]
