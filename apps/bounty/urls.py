from django.urls import path

from . import dialogs, views

app_name = "bounty"

urlpatterns = [
    path("", views.bounty_list, name="list"),
    path("new/", views.bounty_create, name="create"),
    path("mine/", views.my_bounties, name="mine"),
    path("<int:pk>/", views.bounty_detail, name="detail"),
    path("<int:pk>/claim/", views.bounty_claim, name="claim"),
    path("<int:pk>/report/", views.bounty_report, name="report"),
    path(
        "<int:pk>/cancel/",
        dialogs.CancelBountyDialog.as_view(),
        name="cancel_dialog",
    ),
]
