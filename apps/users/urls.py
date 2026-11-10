"""
apps.users.urls
Enterprise-grade URL configuration for the platform Users module.
"""

from allauth.account.views import LogoutView
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import (
    api,
    api_auth,
    api_social,
    mfa_views,
    views_announcements,
    views_notifications,
)
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
    locale_hint,
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
from .views_password import password_rules_check

profile_api_router = DefaultRouter()
profile_api_router.register("profiles", api.ProfileViewSet, basename="profiles")
profile_api_router.register("following", api.FollowingViewSet, basename="following")
profile_api_router.register("activity", api.ActivityFeedViewSet, basename="activity")

app_name = "users"

urlpatterns = [
    path("api/", include(profile_api_router.urls)),
    path("api/auth/signup/", api_auth.ApiSignupView.as_view(), name="api_signup"),
    path("api/auth/login/", api_auth.ApiLoginView.as_view(), name="api_login"),
    path(
        "api/auth/google/",
        api_auth.ApiGoogleLoginView.as_view(),
        name="api_google_login",
    ),
    path(
        "api/auth/password-reset/",
        api_auth.ApiPasswordResetView.as_view(),
        name="api_password_reset",
    ),
    path("api/auth/logout/", api_auth.ApiLogoutView.as_view(), name="api_logout"),
    path(
        "api/auth/me/", api_auth.ApiCurrentUserView.as_view(), name="api_current_user"
    ),
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
    path("mfa/setup/", mfa_views.mfa_setup, name="mfa_setup"),
    path("mfa/verify/", mfa_views.mfa_verify, name="mfa_verify"),
    path("mfa/backup-codes/", mfa_views.mfa_backup_codes, name="mfa_backup_codes"),
    path("mfa/disable/", mfa_views.mfa_disable, name="mfa_disable"),
    path("verify-email/", verify_email_view, name="verify_email"),
    path("verify-email/status/", verify_email_status, name="verify_email_status"),
    path("verify-email/required/", verify_email_required, name="verify_email_required"),
    path("tell-us-about-you/", tell_us_about_you, name="tell_us_about_you"),
    path("password-rules/", password_rules_check, name="password_rules"),
    path("locale-hint/", locale_hint, name="locale_hint"),
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
        "announcements/<int:pk>/dismiss/",
        views_announcements.dismiss_announcement,
        name="dismiss_announcement",
    ),
]

# Admin API endpoints (add to main admin URLconf via the platform/urls.py)
admin_api_patterns = [
    path(
        "admin/users/notification/stats/",
        api.notification_stats,
        name="notification_stats_api",
    ),
    # Public info APIs (for login page); declared before the <provider_id> routes
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
]

urlpatterns += admin_api_patterns
