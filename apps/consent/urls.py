from django.urls import path

from . import views

app_name = "consent"

urlpatterns = [
    path("", views.privacy_centre, name="privacy_centre"),
    path("save/", views.save_choice, name="save"),
]
