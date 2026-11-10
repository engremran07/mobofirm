"""URLs of the central search: results page and combobox suggestions."""

from django.urls import path

from . import views

app_name = "search"

urlpatterns = [
    path("", views.results, name="results"),
    path("suggest/", views.suggest, name="suggest"),
    path("popular/", views.popular, name="popular"),
]
