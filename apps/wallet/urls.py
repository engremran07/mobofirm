from django.urls import path

from . import views

app_name = "wallet"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("ledger/", views.ledger, name="ledger"),
    path("earn/", views.earn, name="earn"),
    path("spend/", views.spend, name="spend"),
    path("topup/", views.topup, name="topup"),
    path("topup/<int:pk>/cancel/", views.cancel_topup, name="cancel_topup"),
    path("payouts/", views.payouts, name="payouts"),
]
