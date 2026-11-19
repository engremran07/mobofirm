from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    # The plans page is the app root: /pricing/. ``index`` stays reversible for old callers.
    path("", views.pricing, name="pricing"),
    path("", views.pricing, name="index"),
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
    path(
        "unlock/<str:firmware_type>/<uuid:firmware_id>/",
        views.unlock_panel,
        name="unlock_panel",
    ),
    path(
        "unlock/<str:firmware_type>/<uuid:firmware_id>/start/",
        views.unlock_start,
        name="unlock_start",
    ),
    path("orders/", views.order_history, name="order_history"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
]
