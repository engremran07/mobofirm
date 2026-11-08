from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "shop"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="shop:pricing"), name="index"),
    path("pricing/", views.pricing, name="pricing"),
    path("packages/<slug:slug>/", views.package_detail, name="package_detail"),
    path("packages/<slug:slug>/order/", views.place_order, name="place_order"),
    path(
        "packages/<slug:slug>/credits/",
        views.package_credits,
        name="package_credits",
    ),
    path(
        "request-file/<str:firmware_type>/<uuid:firmware_id>/",
        views.request_file,
        name="request_file",
    ),
    path(
        "file-credits/<str:firmware_type>/<uuid:firmware_id>/",
        views.file_credits,
        name="file_credits",
    ),
    path("my-package/", views.my_package, name="my_package"),
    path("orders/", views.order_history, name="order_history"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
]
