from django.urls import path

from . import views

app_name = "pages"

urlpatterns = [
    path("", views.page_index, name="index"),
    path("<slug:slug>/", views.page_detail, name="page"),
]
