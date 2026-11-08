from django.urls import path

from . import views

app_name = "referral"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("r/<str:code>/", views.landing, name="landing"),
]
