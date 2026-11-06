from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    path("", views.pricing, name="pricing"),
    path("products/", views.product_list, name="product_list"),
    path("products/<slug:slug>/", views.product_detail, name="product_detail"),
    path("products/<slug:slug>/order/", views.place_order, name="place_order"),
    path("subscriptions/", views.my_subscriptions, name="my_subscriptions"),
    path("orders/", views.order_history, name="order_history"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
]
