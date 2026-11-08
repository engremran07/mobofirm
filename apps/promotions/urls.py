from django.urls import path

from . import dialogs, views

app_name = "promotions"

urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:slug>/", views.offer, name="offer"),
    path("<slug:slug>/claim/", dialogs.ClaimDialog.as_view(), name="claim"),
]
