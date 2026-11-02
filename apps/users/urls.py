"""
apps.users.urls
Enterprise-grade URL configuration for the platform Users module.
"""

from allauth.account.views import LogoutView
from django.urls import path

from . import admin_views, api, views_notifications
from .views import (
    EnterpriseLoginView,
    EnterpriseSignupView,
    approve_device,
    auth_hub_view,
    change_username,
    check_username,
    dashboard_view,
    device_approval_needed,
    device_eviction,
    device_mfa_challenge,
    devices_view,
    notification_settings,
    profile_view,
    push_subscription,
    resend_verification,
    tell_us_about_you,
    unsubscribe_push,
    verify_email_required,
    verify_email_status,
    verify_email_view,
)

app_name = "users"

urlpatterns = [
    path("auth/", auth_hub_view, name="auth_hub"),
    path("login/", EnterpriseLoginView.as_view(), name="account_login"),
    path("signup/", EnterpriseSignupView.as_view(), name="account_signup"),
    path("logout/", LogoutView.as_view(), name="account_logout"),
    path("dashboard/", dashboard_view, name="dashboard"),
    path("profile/", profile_view, name="profile"),
    path("devices/", devices_view, name="devices"),
    path("devices/approval/", device_approval_needed, name="device_approval_needed"),
    path("devices/approve/", approve_device, name="approve_device"),
    path("devices/evict/", device_eviction, name="device_eviction"),
    path("devices/mfa/", device_mfa_challenge, name="device_mfa_challenge"),
    path("verify-email/", verify_email_view, name="verify_email"),
    path("verify-email/status/", verify_email_status, name="verify_email_status"),
    path("verify-email/required/", verify_email_required, name="verify_email_required"),
    path("tell-us-about-you/", tell_us_about_you, name="tell_us_about_you"),
    path(
        "accounts/resend-verification/", resend_verification, name="resend_verification"
    ),
    path("accounts/change-username/", change_username, name="change_username"),
    path("accounts/check-username/", check_username, name="check_username"),
    path(
        "notifications/unread.json",
        api.notifications_unread_json,
        name="notifications_unread_json",
    ),
    path(
        "notifications/count/unread/",
        views_notifications.notification_unread_count,
        name="notification_unread_count",
    ),
    path("notifications/", views_notifications.notification_list, name="notifications"),
    path(
        "notifications/<int:pk>/",
        views_notifications.notification_detail,
        name="notification_detail",
    ),
    path(
        "notifications/mark/<int:pk>/",
        views_notifications.notification_mark_read,
        name="notification_mark_read",
    ),
    path(
        "notifications/mark-all/",
        views_notifications.notification_mark_all_read,
        name="notification_mark_all",
    ),
    path(
        "notifications/settings/", notification_settings, name="notification_settings"
    ),
    path("notifications/push/subscribe/", push_subscription, name="push_subscription"),
    path("notifications/push/unsubscribe/", unsubscribe_push, name="unsubscribe_push"),
    path(
        "admin/notification-dashboard/",
        admin_views.notification_dashboard,
        name="notification_dashboard",
    ),
    path(
        "api/password-reset/verify/",
        api.password_reset_verify,
        name="password_reset_verify",
    ),
]

# Import social API
from . import api_social  # noqa: E402

# Admin API endpoints (add to main admin URLconf via the platform/urls.py)
admin_api_patterns = [
    path(
        "admin/users/notification/stats/",
        api.notification_stats,
        name="notification_stats_api",
    ),
    # Social Auth Provider APIs
    path(
        "api/social-providers/",
        api_social.social_providers_list,
        name="social_providers_api",
    ),
    path(
        "api/social-providers/create/",
        api_social.social_provider_create,
        name="social_provider_create_api",
    ),
    path(
        "api/social-providers/<str:provider_id>/",
        api_social.social_provider_detail,
        name="social_provider_detail_api",
    ),
    path(
        "api/social-providers/<str:provider_id>/test/",
        api_social.social_provider_test,
        name="social_provider_test_api",
    ),
    path(
        "api/social-providers/<str:provider_id>/sync/",
        api_social.social_provider_sync,
        name="social_provider_sync_api",
    ),
    # Social Posting Account APIs
    path(
        "api/social-posting/", api_social.social_posting_list, name="social_posting_api"
    ),
    path(
        "api/social-posting/create/",
        api_social.social_posting_create,
        name="social_posting_create_api",
    ),
    path(
        "api/social-posting/<str:account_id>/",
        api_social.social_posting_detail,
        name="social_posting_detail_api",
    ),
    path(
        "api/social-posting/<str:account_id>/test/",
        api_social.social_posting_test,
        name="social_posting_test_api",
    ),
    path(
        "api/social-posting/<str:account_id>/send-test/",
        api_social.social_posting_send_test,
        name="social_posting_send_test_api",
    ),
    # Public info APIs (for login page)
    path(
        "api/social-providers/info/",
        api_social.social_providers_info,
        name="social_providers_info_api",
    ),
    path(
        "api/social-platforms/info/",
        api_social.social_platforms_info,
        name="social_platforms_info_api",
    ),
]

urlpatterns += admin_api_patterns
