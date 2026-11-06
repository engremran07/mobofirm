from django.urls import path

from . import views

app_name = "storage"

urlpatterns = [
    path("", views.status, name="status"),
    path("locations/<uuid:location_id>/verify/", views.verify, name="verify"),
    path("locations/verify-pending/", views.verify_pending, name="verify_pending"),
]
