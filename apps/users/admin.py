# apps/users/admin.py
"""
apps.users.admin
Enterprise admin interfaces for user-related models in the platform.

- Export support (import_export) when installed
- Does NOT break when import_export is absent
- ZERO silent errors
- Django 5.x compatible

IMPORTANT FIX:
--------------
ExportMixin **does not subclass ModelAdmin**, so we must ALWAYS
wrap it inside a ModelAdmin subclass to avoid:

    ValueError: Wrapped class must subclass ModelAdmin.

This file includes a safe BaseAdminClass that prevents the crash
while preserving your export features.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.core.exceptions import FieldError
from django.db import DatabaseError
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Optional import_export integration — fixed so it never breaks admin
# --------------------------------------------------------------------------
try:
    from import_export.admin import ExportMixin  # type: ignore

    _HAS_IMPORT_EXPORT = True
except ImportError:
    ExportMixin = None
    _HAS_IMPORT_EXPORT = False


# --------------------------------------------------------------------------
# MODELS (exactly as present in your models.py)
# --------------------------------------------------------------------------
from .models import (  # noqa: E402
    Announcement,
    CustomUser,
    MFADevice,
    Notification,
    NotificationPreferences,
    PushSubscription,
    SecurityQuestion,
    UsersSettings,
)

# ==========================================================================
# FIXED BASE ADMIN CLASS
# ==========================================================================
# If import_export is available:
#   class BaseAdminClass(ExportMixin, admin.ModelAdmin)
# else:
#   class BaseAdminClass(admin.ModelAdmin)
# This guarantees that @admin.register(...) always receives a ModelAdmin subclass.

_ExportBase: type = ExportMixin if (_HAS_IMPORT_EXPORT and ExportMixin) else object


class BaseAdminClass(_ExportBase, admin.ModelAdmin):  # type: ignore[misc]
    """Safe hybrid admin class — uses ExportMixin when import_export is installed."""


# ==========================================================================
# CustomUser Admin
# ==========================================================================
@admin.register(CustomUser)
class CustomUserAdmin(BaseAdminClass):  # type: ignore[misc]
    """Enterprise-grade admin for CustomUser."""

    list_display = (
        "email",
        "username",
        "full_name",
        "is_active",
        "is_staff",
        "is_superuser",
        "credits",
        "signup_method",
        "email_verified_at",
        "date_joined",
    )

    search_fields = (
        "email",
        "username",
        "full_name",
        "phone",
    )

    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        "signup_method",
        "email_verified_at",
    )

    readonly_fields = (
        "date_joined",
        "last_unlock",
    )

    ordering = ("-date_joined",)
    save_on_top = True

    list_select_related = ()

    fieldsets = (
        (_("Authentication"), {"fields": ("email", "username", "password")}),
        (_("Personal Info"), {"fields": ("full_name", "phone")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            _("Additional Info"),
            {
                "fields": (
                    "credits",
                    "signup_method",
                    "last_unlock",
                    "email_verified_at",
                    "verification_code",
                    "date_joined",
                )
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[CustomUser]:
        """Optimize user queryset with prefetch_related for groups."""
        qs = super().get_queryset(request)
        try:
            return qs.prefetch_related("groups")
        except (FieldError, DatabaseError):
            logger.debug("CustomUserAdmin.get_queryset prefetch failed", exc_info=True)
            return qs

    # ------------------------------------------------------------------
    # Admin action: mark selected users as email verified
    # ------------------------------------------------------------------
    @admin.action(description="Mark selected users as email verified (set now)")
    def mark_email_verified(
        self, request: HttpRequest, queryset: QuerySet[CustomUser]
    ) -> None:
        """Mark selected users as email verified with optional allauth sync."""
        updated = queryset.filter(email_verified_at__isnull=True).update(
            email_verified_at=timezone.now()
        )
        # Sync allauth EmailAddress if installed
        try:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.filter(user__in=queryset).update(
                verified=True, primary=True
            )
        except ImportError:
            logger.debug("EmailAddress sync skipped or failed", exc_info=True)

        if updated:
            self.message_user(
                request, _(f"{updated} user(s) marked as verified."), messages.SUCCESS
            )
        else:
            self.message_user(request, _("No users updated."), messages.INFO)

    @admin.action(description="Clear email verification (set to unverified)")
    def clear_email_verification(
        self, request: HttpRequest, queryset: QuerySet[CustomUser]
    ) -> None:
        """Remove email verification status from selected users."""
        count = queryset.update(email_verified_at=None, verification_code="")
        try:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.filter(user__in=queryset).update(verified=False)
        except ImportError:
            logger.debug("EmailAddress unverify sync skipped or failed", exc_info=True)
        if count:
            self.message_user(request, _("%d user(s) marked unverified.") % count)
        else:
            self.message_user(request, _("No users updated."), messages.INFO)

    actions = ["mark_email_verified", "clear_email_verification"]


# ==========================================================================
# Notification Admin
# ==========================================================================
@admin.register(Notification)
class NotificationAdmin(BaseAdminClass):
    """Admin interface for Notifications."""

    list_display = (
        "recipient_display",
        "title",
        "priority",
        "channel",
        "is_read",
        "created_at",
        "read_at",
    )

    list_filter = ("priority", "channel", "is_read", "created_at")
    search_fields = ("title", "message", "recipient__email", "recipient__username")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "read_at")
    list_select_related = ("recipient",)
    save_on_top = True

    actions = ["mark_selected_read"]

    if _HAS_IMPORT_EXPORT:
        actions.append("export_selected_as_csv")

    @admin.display(description=_("Recipient"))
    def recipient_display(self, obj: Notification) -> str:
        return (
            getattr(obj.recipient, "email", None)
            or getattr(obj.recipient, "username", None)
            or "Anonymous"
        )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Notification]:
        """Optimize notification queryset with recipient select_related."""
        qs = super().get_queryset(request)
        try:
            return qs.select_related("recipient")
        except (FieldError, DatabaseError):
            logger.debug("NotificationAdmin.get_queryset failed", exc_info=True)
            return qs

    def mark_selected_read(
        self, request: HttpRequest, queryset: QuerySet[Notification]
    ) -> None:
        """Mark selected notifications as read."""
        try:
            updated = queryset.filter(is_read=False).update(is_read=True)
            self.message_user(request, _("%d notifications marked as read.") % updated)
        except DatabaseError as exc:
            logger.exception("Failed to mark notifications read: %s", exc)
            self.message_user(
                request,
                _("Failed to mark notifications as read."),
                level=messages.ERROR,
            )

    def export_selected_as_csv(
        self, request: HttpRequest, _queryset: QuerySet[Notification]
    ) -> None:
        """Provide export instructions to user."""
        self.message_user(
            request, _("Use the Export button above to export notifications.")
        )


# ==========================================================================
# Announcement Admin
# ==========================================================================
@admin.register(Announcement)
class AnnouncementAdmin(BaseAdminClass):
    """Admin for announcements."""

    list_display = (
        "title",
        "audience",
        "is_global",
        "created_by_display",
        "start_at",
        "expires_at",
        "is_active_display",
    )

    search_fields = ("title", "message")
    list_filter = ("audience", "is_global", "expires_at")
    readonly_fields = ("created_by",)
    ordering = ("-start_at",)
    save_on_top = True
    actions = ["publish_selected", "unpublish_selected"]

    def save_model(self, request, obj, form, change):
        if not change and not obj.created_by:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description=_("Created By"))
    def created_by_display(self, obj: Announcement):
        return (
            getattr(obj.created_by, "email", None)
            or getattr(obj.created_by, "username", None)
            or "—"
        )

    @admin.display(description=_("Active?"))
    def is_active_display(self, obj: Announcement):
        try:
            return "✅" if getattr(obj, "is_active", False) else "❌"
        except (AttributeError, TypeError):
            return "—"

    def publish_selected(self, request, queryset):
        try:
            count = queryset.update(is_active=True)
            self.message_user(request, _("%d announcements published.") % count)
        except DatabaseError:
            logger.exception("Failed to publish announcements")
            self.message_user(
                request, _("Failed to publish announcements."), level=messages.ERROR
            )

    def unpublish_selected(self, request, queryset):
        try:
            count = queryset.update(is_active=False)
            self.message_user(request, _("%d announcements unpublished.") % count)
        except DatabaseError:
            logger.exception("Failed to unpublish announcements")
            self.message_user(
                request, _("Failed to unpublish announcements."), level=messages.ERROR
            )


# ==========================================================================
# Admin Branding
# ==========================================================================
admin.site.site_header = _("Site Administration")
admin.site.index_title = _("Control Panel")
admin.site.site_title = _("Site Admin Portal")

# Users app settings (singleton)
try:
    from solo.admin import SingletonModelAdmin

    @admin.register(UsersSettings)
    class UsersSettingsAdmin(SingletonModelAdmin):
        """Manage Users app configuration independently."""

        fieldsets = (
            (
                _("Access & Flows"),
                {"fields": ("enable_signup", "enable_password_reset")},
            ),
            (
                _("Notifications System"),
                {
                    "fields": ("enable_notifications",),
                    "description": "Master switch for all notifications. When disabled, no notifications will be sent.",
                },
            ),
            (
                _("Security"),
                {
                    "fields": (
                        "require_mfa",
                        "mfa_totp_issuer",
                        "max_login_attempts",
                        "rate_limit_window_seconds",
                    )
                },
            ),
            (
                _("reCAPTCHA"),
                {"fields": ()},
            ),
            (_("Profile Completion"), {"fields": ("required_profile_fields",)}),
            (_("Payments"), {"fields": ("enable_payments",)}),
        )

        readonly_fields = ("notification_stats",)

        def has_add_permission(self, request):
            return False

        def notification_stats(self, _obj: object) -> str:
            """Display notification system statistics."""
            from django.utils.html import format_html

            try:
                total_users = CustomUser.objects.filter(is_active=True).count()
                prefs_count = NotificationPreferences.objects.count()  # type: ignore[attr-defined]
                push_count = PushSubscription.objects.filter(is_active=True).count()  # type: ignore[attr-defined]
                unread_notifs = Notification.objects.filter(is_read=False).count()  # type: ignore[attr-defined]

                return format_html(
                    "<strong>System Status:</strong><br/>"
                    "✅ Active Users: {}<br/>"
                    "⚙️ Users with Preferences: {} ({:.1f}%)<br/>"
                    "🔔 Active Push Subscriptions: {}<br/>"
                    "📬 Unread Notifications: {}<br/>"
                    "<br/>"
                    '<a href="/admin/users/notificationpreferences/" class="button">Manage Preferences</a> '
                    '<a href="/admin/users/pushsubscription/" class="button">Manage Subscriptions</a>',
                    total_users,
                    prefs_count,
                    (prefs_count / total_users * 100) if total_users > 0 else 0,
                    push_count,
                    unread_notifs,
                )
            except DatabaseError as exc:
                logger.exception("Failed to get notification stats: %s", exc)
                return "Error loading stats"
except ImportError:
    logger.warning("solo not available; UsersSettings admin not registered")


# ==========================================================================
# NotificationPreferences Admin
# ==========================================================================
@admin.register(NotificationPreferences)
class NotificationPreferencesAdmin(BaseAdminClass):
    """Admin for user notification preferences with bulk actions."""

    list_display = (
        "user_email",
        "email_frequency",
        "push_enabled_display",
        "quiet_hours_display",
        "email_prefs_summary",
        "web_prefs_summary",
        "updated_at",
    )

    list_filter = (
        "email_frequency",
        "push_enabled",
        "quiet_hours_enabled",
        "email_comments",
        "email_replies",
        "email_mentions",
        "web_comments",
        "web_awards",
    )

    search_fields = ("user__email", "user__username", "user__full_name")

    readonly_fields = ("user", "created_at", "updated_at")

    fieldsets = (
        (_("User"), {"fields": ("user", "created_at", "updated_at")}),
        (
            _("Email Notifications"),
            {
                "fields": (
                    "email_frequency",
                    "email_comments",
                    "email_replies",
                    "email_mentions",
                    "email_new_posts",
                    "email_security",
                ),
                "description": "Control when and what type of email notifications to send",
            },
        ),
        (
            _("Web Notifications"),
            {
                "fields": (
                    "web_comments",
                    "web_awards",
                    "web_moderation",
                    "web_system",
                ),
                "description": "Control in-app notification types",
            },
        ),
        (
            _("Push Notifications"),
            {
                "fields": ("push_enabled",),
                "description": "Browser push notifications status",
            },
        ),
        (
            _("Quiet Hours"),
            {
                "fields": (
                    "quiet_hours_enabled",
                    "quiet_hours_start",
                    "quiet_hours_end",
                ),
                "description": "Mute non-critical notifications during specific hours",
            },
        ),
    )

    ordering = ("-updated_at",)
    list_per_page = 50
    date_hierarchy = "updated_at"
    save_on_top = True

    actions = [
        "enable_all_email",
        "disable_all_email",
        "set_email_instant",
        "set_email_daily",
        "enable_push_all",
        "disable_push_all",
        "enable_quiet_hours",
        "disable_quiet_hours",
        "reset_to_defaults",
    ]

    @admin.display(description=_("User"))
    def user_email(self, obj: NotificationPreferences):
        return obj.user.email

    @admin.display(description=_("Push"), boolean=True)
    def push_enabled_display(self, obj: NotificationPreferences):
        return obj.push_enabled

    @admin.display(description=_("Quiet Hours"))
    def quiet_hours_display(self, obj: NotificationPreferences):
        if obj.quiet_hours_enabled:
            return f"🌙 {obj.quiet_hours_start} - {obj.quiet_hours_end}"
        return "—"

    @admin.display(description=_("Email Preferences"))
    def email_prefs_summary(self, obj: NotificationPreferences):
        enabled = []
        if obj.email_comments:
            enabled.append("💬")
        if obj.email_replies:
            enabled.append("↩️")
        if obj.email_mentions:
            enabled.append("@")
        if obj.email_new_posts:
            enabled.append("📝")
        return " ".join(enabled) if enabled else "None"

    @admin.display(description=_("Web Preferences"))
    def web_prefs_summary(self, obj: NotificationPreferences):
        enabled = []
        if obj.web_comments:
            enabled.append("💬")
        if obj.web_awards:
            enabled.append("🏆")
        if obj.web_moderation:
            enabled.append("🚨")
        if obj.web_system:
            enabled.append("📢")
        return " ".join(enabled) if enabled else "None"

    # Bulk Actions
    @admin.action(description=_("Enable all email notifications"))
    def enable_all_email(self, request, queryset):
        try:
            updated = queryset.update(
                email_comments=True,
                email_replies=True,
                email_mentions=True,
                email_new_posts=True,
            )
            self.message_user(
                request,
                _("Enabled all email notifications for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to enable email notifications: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Disable all email notifications"))
    def disable_all_email(self, request, queryset):
        try:
            updated = queryset.update(
                email_comments=False,
                email_replies=False,
                email_mentions=False,
                email_new_posts=False,
                email_frequency="never",
            )
            self.message_user(
                request,
                _("Disabled all email notifications for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to disable email notifications: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Set email frequency to instant"))
    def set_email_instant(self, request, queryset):
        try:
            updated = queryset.update(email_frequency="instant")
            self.message_user(
                request,
                _("Set instant email frequency for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to set email frequency: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Set email frequency to daily digest"))
    def set_email_daily(self, request, queryset):
        try:
            updated = queryset.update(email_frequency="daily")
            self.message_user(
                request,
                _("Set daily digest for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to set email frequency: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Enable push notifications"))
    def enable_push_all(self, request, queryset):
        try:
            updated = queryset.update(push_enabled=True)
            self.message_user(
                request,
                _("Enabled push notifications for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to enable push: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Disable push notifications"))
    def disable_push_all(self, request, queryset):
        try:
            updated = queryset.update(push_enabled=False)
            self.message_user(
                request,
                _("Disabled push notifications for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to disable push: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Enable quiet hours (22:00-08:00)"))
    def enable_quiet_hours(self, request, queryset):
        try:
            updated = queryset.update(
                quiet_hours_enabled=True,
                quiet_hours_start="22:00",
                quiet_hours_end="08:00",
            )
            self.message_user(
                request,
                _("Enabled quiet hours for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to enable quiet hours: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Disable quiet hours"))
    def disable_quiet_hours(self, request, queryset):
        try:
            updated = queryset.update(quiet_hours_enabled=False)
            self.message_user(
                request,
                _("Disabled quiet hours for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to disable quiet hours: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Reset preferences to defaults"))
    def reset_to_defaults(self, request, queryset):
        try:
            updated = queryset.update(
                email_comments=True,
                email_replies=True,
                email_mentions=True,
                email_new_posts=False,
                email_security=True,
                email_frequency="instant",
                web_comments=True,
                web_awards=True,
                web_moderation=True,
                web_system=True,
                push_enabled=False,
                quiet_hours_enabled=False,
            )
            self.message_user(
                request,
                _("Reset preferences to defaults for %d users.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to reset preferences: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)


# ==========================================================================
# PushSubscription Admin
# ==========================================================================
@admin.register(PushSubscription)
class PushSubscriptionAdmin(BaseAdminClass):
    """Admin for push notification subscriptions."""

    list_display = (
        "user_email",
        "device_name_display",
        "browser_info",
        "is_active",
        "created_at",
        "last_used_at",
        "subscription_age",
    )

    list_filter = (
        "is_active",
        "created_at",
        "last_used_at",
    )

    search_fields = (
        "user__email",
        "user__username",
        "device_name",
        "user_agent",
        "endpoint",
    )

    readonly_fields = (
        "user",
        "endpoint",
        "p256dh",
        "auth",
        "user_agent",
        "created_at",
        "last_used_at",
        "subscription_details",
    )

    fieldsets = (
        (
            _("User & Device"),
            {"fields": ("user", "device_name", "user_agent", "is_active")},
        ),
        (
            _("Subscription Details"),
            {
                "fields": ("endpoint", "p256dh", "auth", "subscription_details"),
                "classes": ("collapse",),
                "description": "Web Push subscription technical details",
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "last_used_at"),
            },
        ),
    )

    ordering = ("-created_at",)
    list_per_page = 50
    date_hierarchy = "created_at"
    save_on_top = True

    actions = [
        "activate_selected",
        "deactivate_selected",
        "test_push_notification",
        "cleanup_old_subscriptions",
    ]

    @admin.display(description=_("User"))
    def user_email(self, obj: PushSubscription):
        return obj.user.email

    @admin.display(description=_("Device"))
    def device_name_display(self, obj: PushSubscription):
        return obj.device_name or "Unknown Device"

    @admin.display(description=_("Browser"))
    def browser_info(self, obj: PushSubscription):
        ua = obj.user_agent or ""
        if "Chrome" in ua:
            return "🌐 Chrome"
        elif "Firefox" in ua:
            return "🦊 Firefox"
        elif "Safari" in ua:
            return "🧭 Safari"
        elif "Edge" in ua:
            return "⚡ Edge"
        elif "Opera" in ua:
            return "🎭 Opera"
        return "❓ Unknown"

    @admin.display(description=_("Age"))
    def subscription_age(self, obj: PushSubscription):
        delta = timezone.now() - obj.created_at
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            return f"{hours}h"
        elif days < 30:
            return f"{days}d"
        elif days < 365:
            months = days // 30
            return f"{months}mo"
        else:
            years = days // 365
            return f"{years}y"

    @admin.display(description=_("Subscription Info"))
    def subscription_details(self, obj: PushSubscription):
        from django.utils.html import format_html

        return format_html(
            "<strong>Endpoint:</strong><br/>{}<br/><br/>"
            "<strong>P256DH Key:</strong><br/>{}<br/><br/>"
            "<strong>Auth Secret:</strong><br/>{}<br/><br/>"
            "<strong>Status:</strong> {}<br/>"
            "<strong>Last Used:</strong> {}",
            obj.endpoint[:80] + "..." if len(obj.endpoint) > 80 else obj.endpoint,
            obj.p256dh[:50] + "..." if len(obj.p256dh) > 50 else obj.p256dh,
            obj.auth[:50] + "..." if len(obj.auth) > 50 else obj.auth,
            "✅ Active" if obj.is_active else "❌ Inactive",
            obj.last_used_at.strftime("%Y-%m-%d %H:%M:%S")
            if obj.last_used_at
            else "Never",
        )

    # Bulk Actions
    @admin.action(description=_("Activate selected subscriptions"))
    def activate_selected(self, request, queryset):
        try:
            updated = queryset.update(is_active=True)
            self.message_user(
                request,
                _("Activated %d subscriptions.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to activate subscriptions: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Deactivate selected subscriptions"))
    def deactivate_selected(self, request, queryset):
        try:
            updated = queryset.update(is_active=False)
            self.message_user(
                request,
                _("Deactivated %d subscriptions.") % updated,
                messages.SUCCESS,
            )
        except DatabaseError as exc:
            logger.exception("Failed to deactivate subscriptions: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Send test push notification"))
    def test_push_notification(self, request, queryset):
        """Send a test notification to selected subscriptions."""
        try:
            from apps.users.services.notifications import send_notification

            success_count = 0
            for subscription in queryset.filter(is_active=True):
                try:
                    send_notification(
                        recipient=subscription.user,
                        title="Test Notification from Admin",
                        message=f"This is a test push notification sent to {subscription.device_name or 'your device'}",
                        action_type="system",
                        icon="bell",
                    )
                    success_count += 1
                except (OSError, RuntimeError, ValueError) as exc:
                    logger.warning("Failed to send test notification: %s", exc)

            self.message_user(
                request,
                _("Sent test notifications to %d subscriptions.") % success_count,
                messages.SUCCESS,
            )
        except (ImportError, DatabaseError) as exc:
            logger.exception("Failed to send test notifications: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)

    @admin.action(description=_("Cleanup old/inactive subscriptions (30+ days)"))
    def cleanup_old_subscriptions(self, request, queryset):
        """Remove subscriptions inactive for 30+ days."""
        try:
            from datetime import timedelta

            threshold = timezone.now() - timedelta(days=30)

            old_inactive = queryset.filter(is_active=False, last_used_at__lt=threshold)
            count = old_inactive.count()
            old_inactive.delete()

            self.message_user(
                request,
                _("Deleted %d old inactive subscriptions.") % count,
                messages.SUCCESS,
            )
        except (DatabaseError, ValueError) as exc:
            logger.exception("Failed to cleanup subscriptions: %s", exc)
            self.message_user(request, _("Operation failed."), messages.ERROR)


# ==============================================================================
# Social Account Provider Admin (Enhanced)
# ==============================================================================
from allauth.socialaccount.admin import SocialAppAdmin as BaseSocialAppAdmin  # noqa: E402, I001
from allauth.socialaccount.models import SocialApp  # noqa: E402

# Unregister the default admin
admin.site.unregister(SocialApp)


@admin.register(SocialApp)
class EnhancedSocialAppAdmin(BaseSocialAppAdmin):
    """Enhanced admin for social authentication providers with helpful documentation."""

    list_display = (
        "name",
        "provider",
        "status_display",
        "sites_list",
        "modified_display",
    )
    list_filter = ("provider",)
    search_fields = ("name", "provider", "client_id")
    filter_horizontal = ("sites",)

    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "name"),
                "description": "Configure OAuth providers for social authentication.",
            },
        ),
        (
            "OAuth Credentials",
            {
                "fields": ("client_id", "secret", "key"),
                "description": (
                    "Get OAuth credentials from provider developer consoles:<br>"
                    "• <strong>Google:</strong> <a href='https://console.cloud.google.com/apis/credentials' target='_blank'>Google Cloud Console</a><br>"
                    "• <strong>Facebook:</strong> <a href='https://developers.facebook.com/apps/' target='_blank'>Facebook Developers</a><br>"
                    "• <strong>Microsoft:</strong> <a href='https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade' target='_blank'>Azure Portal</a><br>"
                    "• <strong>GitHub:</strong> <a href='https://github.com/settings/developers' target='_blank'>GitHub Settings</a>"
                ),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("sites", "settings"),
                "description": "Select which sites this provider is enabled for.",
            },
        ),
    )

    def status_display(self, obj):
        """Show if provider has valid-looking credentials."""
        if obj.client_id.startswith("your-") or len(obj.client_id) < 10:
            return "⚠️ Test/Placeholder"
        return "✓ Configured"

    status_display.short_description = "Status"

    def sites_list(self, obj):
        """Display associated sites."""
        sites = obj.sites.all()
        if not sites:
            return "❌ No sites"
        return ", ".join(site.domain for site in sites[:3])

    sites_list.short_description = "Sites"

    def modified_display(self, obj):
        """Show when last modified."""
        if hasattr(obj, "modified"):
            return obj.modified.strftime("%Y-%m-%d %H:%M")
        return "-"

    modified_display.short_description = "Last Modified"

    def get_readonly_fields(self, request, obj=None):
        """Make provider readonly when editing existing apps."""
        if obj:  # editing existing
            return ("provider",)
        return ()


@admin.register(SecurityQuestion)
class SecurityQuestionAdmin(admin.ModelAdmin[SecurityQuestion]):  # type: ignore[type-arg]
    list_display = ["user", "question_key", "created_at"]
    list_filter = ["question_key", "created_at"]
    search_fields = ["user__email", "user__username"]
    readonly_fields = ["created_at"]


@admin.register(MFADevice)
class MFADeviceAdmin(admin.ModelAdmin[MFADevice]):  # type: ignore[type-arg]
    list_display = ["user", "device_type", "name", "is_active", "created_at"]
    list_filter = ["device_type", "is_active"]
    search_fields = ["user__email", "user__username", "name"]
    readonly_fields = ["created_at", "last_used_at"]
