from django.urls import path

from . import views

app_name = "marketplace"

urlpatterns = [
    path("", views.listing_browse, name="listing_browse"),
    path("listings/<int:pk>/", views.listing_detail, name="listing_detail"),
    path("listings/<int:pk>/toggle/", views.toggle_listing, name="toggle_listing"),
    path("sellers/<int:pk>/", views.seller_profile, name="seller_profile"),
    path("sell/", views.become_seller, name="become_seller"),
    path("my-listings/", views.my_listings, name="my_listings"),
    path("verification/", views.request_verification, name="request_verification"),
]
