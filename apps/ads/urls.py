from django.urls import path

from . import dialogs, manager_views, rewarded_views, views

app_name = "ads"

urlpatterns = [
    path("api/placements/", views.list_placements, name="list_placements"),
    path("api/events/", views.record_event, name="record_event"),
    path("api/fill/", views.fill_ad, name="fill_ad"),
    path("api/click/", views.record_click, name="record_click"),
    path(
        "go/<slug:placement>/<int:creative_id>/",
        views.click_redirect,
        name="click_redirect",
    ),
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
    # Rewarded ads: page, then the server-validated start / heartbeat / complete
    path("rewarded/<int:config_id>/", rewarded_views.rewarded_page, name="rewarded"),
    path(
        "rewarded/<int:config_id>/start/",
        rewarded_views.rewarded_start,
        name="rewarded_start",
    ),
    path(
        "rewarded/heartbeat/",
        rewarded_views.rewarded_heartbeat,
        name="rewarded_heartbeat",
    ),
    path(
        "rewarded/complete/",
        rewarded_views.rewarded_complete,
        name="rewarded_complete",
    ),
    # Dashboard
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/toggles/", views.toggle_settings, name="toggle_settings"),
    # Placement manager (staff, admin shell)
    path(
        "manage/placements/",
        manager_views.placement_manager_view,
        name="placement_manager",
    ),
    path("manage/scan/", manager_views.run_scan, name="run_scan"),
    path(
        "manage/placements/place/",
        manager_views.admin_shell(dialogs.PlaceAdDialog.as_view()),
        name="place_ad",
    ),
    path(
        "manage/placements/<int:pk>/toggle/",
        manager_views.toggle_placement,
        name="toggle_placement",
    ),
    path(
        "manage/placements/<int:pk>/remove/",
        manager_views.admin_shell(dialogs.RemovePlacementDialog.as_view()),
        name="remove_placement",
    ),
    path(
        "manage/test-profile/",
        manager_views.set_test_profile,
        name="test_profile",
    ),
    path("manage/suggestions/", manager_views.discoveries_view, name="discoveries"),
    path(
        "manage/suggestions/<int:pk>/approve/",
        manager_views.approve_discovery,
        name="approve_discovery",
    ),
    path(
        "manage/suggestions/approve/",
        manager_views.admin_shell(dialogs.ReviewDiscoveriesDialog.as_view()),
        {"decision": "approve"},
        name="approve_discoveries",
    ),
    path(
        "manage/suggestions/reject/",
        manager_views.admin_shell(dialogs.ReviewDiscoveriesDialog.as_view()),
        {"decision": "reject"},
        name="reject_discoveries",
    ),
]
