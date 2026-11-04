from django.urls import path

from . import views

app_name = "ads"

urlpatterns = [
    path("api/placements/", views.list_placements, name="list_placements"),
    path("api/events/", views.record_event, name="record_event"),
    path("api/fill/", views.fill_ad, name="fill_ad"),
    path("api/click/", views.record_click, name="record_click"),
    # Affiliate products
    path(
        "api/affiliate-click/",
        views.track_affiliate_click,
        name="track_affiliate_click",
    ),
    path(
        "api/affiliate-products/",
        views.get_affiliate_products,
        name="get_affiliate_products",
    ),
    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/toggles/", views.toggle_settings, name="toggle_settings"),
]
