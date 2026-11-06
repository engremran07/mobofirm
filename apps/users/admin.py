"""Admin for accounts, profiles, notifications and security (Unfold)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, ClassVar

from allauth.account.models import EmailAddress
from allauth.socialaccount.admin import SocialAppAdmin as BaseSocialAppAdmin
from allauth.socialaccount.models import SocialApp
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    ChoicesDropdownFilter,
    RangeDateTimeFilter,
)
from unfold.forms import AdminPasswordChangeForm
from unfold.forms import UserChangeForm as UnfoldUserChangeForm
from unfold.forms import UserCreationForm as UnfoldUserCreationForm

from apps.core.admin_base import (
    ModelAdmin,
    SingletonModelAdmin,
    action,
    display,
)

from .models import (
    Announcement,
    CustomUser,
    MFADevice,
    Notification,
    NotificationPreferences,
    PushSubscription,
    SecurityQuestion,
    UsersSettings,
)
from .notification_stats import notification_dashboard_context
from .profile_models import ActivityFeed, Following, Profile, Reputation
from .request_user import current_user

logger = logging.getLogger(__name__)

PRIORITY_LABELS = {
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
    "critical": "danger",
    "urgent": "danger",
}


# --------------------------------------------------------------------------
# Profile and activity
# --------------------------------------------------------------------------
@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    list_display = ("user", "is_public", "updated_at")
    list_filter = ("is_public", ("updated_at", RangeDateTimeFilter))
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)


@admin.register(Reputation)
class ReputationAdmin(ModelAdmin):
    list_display = ("user", "total_score", "fw_contributions", "downloads_helped")
    ordering = ("-total_score",)
    search_fields = ("user__email", "user__username")
    raw_id_fields = ("user",)


@admin.register(Following)
class FollowingAdmin(ModelAdmin):
    list_display = ("follower", "following", "created_at")
    search_fields = ("follower__email", "following__email")
    readonly_fields = ("created_at",)
    raw_id_fields = ("follower", "following")


@admin.register(ActivityFeed)
class ActivityFeedAdmin(ModelAdmin):
    list_display = ("user", "action_type", "created_at")
    list_filter = (
        ("action_type", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    readonly_fields = ("created_at",)
    raw_id_fields = ("user",)


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------
class CustomUserCreationForm(UnfoldUserCreationForm):
    class Meta:
        model = CustomUser
        fields = ("email", "username")


class CustomUserChangeForm(UnfoldUserChangeForm):
    class Meta:
        model = CustomUser
        fields = "__all__"


@admin.register(CustomUser)
class CustomUserAdmin(BaseUserAdmin, ModelAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "email",
        "username",
        "full_name",
        "account_state",
        "is_staff",
        "is_superuser",
        "signup_method",
        "email_verified_at",
        "date_joined",
    )
    list_filter = (
        "is_active",
        "is_staff",
        "is_superuser",
        ("signup_method", ChoicesDropdownFilter),
        ("date_joined", RangeDateTimeFilter),
        ("email_verified_at", RangeDateTimeFilter),
    )
    search_fields = ("email", "username", "full_name", "phone")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_unlock", "last_login")
    filter_horizontal = ("groups", "user_permissions")
    actions = ("mark_email_verified", "clear_email_verification")

    fieldsets = (
        (_("Authentication"), {"fields": ("email", "username", "password")}),
        (_("Personal info"), {"fields": ("full_name", "phone")}),
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
            _("Additional info"),
            {
                "fields": (
                    "credits",
                    "signup_method",
                    "last_unlock",
                    "email_verified_at",
                    "last_login",
                    "date_joined",
                ),
                "classes": ("tab",),
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2"),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[CustomUser]:
        return super().get_queryset(request).prefetch_related("groups")

    @display(
        description=_("State"),
        label={
            "Active": "success",
            "Disabled": "danger",
        },
    )
    def account_state(self, obj: CustomUser) -> str:
        return "Active" if obj.is_active else "Disabled"

    @action(description=_("Mark selected users as email verified"))
    def mark_email_verified(
        self, request: HttpRequest, queryset: QuerySet[CustomUser]
    ) -> None:
        """Mark each user's own email address as verified (superusers only)."""
        if not request.user.is_superuser:
            self.message_user(
                request,
                _("Only superusers may verify email addresses."),
                messages.ERROR,
            )
            return
        updated = 0
        for user in queryset:
            if user.email_verified_at is None:
                user.email_verified_at = timezone.now()
                user.save(update_fields=["email_verified_at"])
                updated += 1
            EmailAddress.objects.filter(user=user, email__iexact=user.email).update(
                verified=True
            )
            self.log_change(request, user, "Email marked verified by administrator.")
        if updated:
            self.message_user(
                request,
                _("%(count)d user(s) marked as verified.") % {"count": updated},
                messages.SUCCESS,
            )
        else:
            self.message_user(request, _("No users updated."), messages.INFO)

    @action(description=_("Clear email verification"))
    def clear_email_verification(
        self, request: HttpRequest, queryset: QuerySet[CustomUser]
    ) -> None:
        """Remove email verification status from selected users (superusers only)."""
        if not request.user.is_superuser:
            self.message_user(
                request,
                _("Only superusers may clear email verification."),
                messages.ERROR,
            )
            return
        count = 0
        for user in queryset:
            user.email_verified_at = None
            user.save(update_fields=["email_verified_at"])
            EmailAddress.objects.filter(user=user).update(verified=False)
            self.log_change(
                request, user, "Email verification cleared by administrator."
            )
            count += 1
        if count:
            self.message_user(
                request, _("%(count)d user(s) marked unverified.") % {"count": count}
            )
        else:
            self.message_user(request, _("No users updated."), messages.INFO)


# --------------------------------------------------------------------------
# Notifications
# --------------------------------------------------------------------------
@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = (
        "recipient_display",
        "title",
        "priority_label",
        "channel",
        "is_read",
        "created_at",
        "read_at",
    )
    list_filter = (
        ("priority", ChoicesDropdownFilter),
        ("channel", ChoicesDropdownFilter),
        "is_read",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("title", "message", "recipient__email", "recipient__username")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "read_at")
    list_select_related = ("recipient",)
    raw_id_fields = ("recipient", "actor")
    actions = ("mark_selected_read",)
    actions_list = ("open_dashboard",)

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "dashboard/",
                self.admin_site.admin_view(self.dashboard_view),
                name="users_notification_dashboard",
            ),
            *super().get_urls(),
        ]

    def dashboard_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            "title": _("Notification dashboard"),
            **notification_dashboard_context(),
        }
        return TemplateResponse(
            request, "admin/users/notification_dashboard.html", context
        )

    @action(
        description=_("Notification dashboard"),
        url_path="open-dashboard",
        icon="notifications_active",
    )
    def open_dashboard(self, request: HttpRequest) -> HttpResponse:
        return redirect("admin:users_notification_dashboard")

    @display(description=_("Recipient"), ordering="recipient__email")
    def recipient_display(self, obj: Notification) -> str:
        return obj.recipient.email or obj.recipient.username or "Anonymous"

    @display(description=_("Priority"), ordering="priority", label=PRIORITY_LABELS)
    def priority_label(self, obj: Notification) -> str:
        return obj.priority

    @action(description=_("Mark selected notifications as read"))
    def mark_selected_read(
        self, request: HttpRequest, queryset: QuerySet[Notification]
    ) -> None:
        updated = queryset.filter(is_read=False).update(is_read=True)
        self.message_user(
            request, _("%(count)d notifications marked as read.") % {"count": updated}
        )


@admin.register(Announcement)
class AnnouncementAdmin(ModelAdmin):
    list_display = (
        "title",
        "audience",
        "is_global",
        "created_by_display",
        "start_at",
        "expires_at",
        "state",
    )
    search_fields = ("title", "message")
    list_filter = (
        ("audience", ChoicesDropdownFilter),
        "is_active",
        "is_global",
        ("expires_at", RangeDateTimeFilter),
    )
    readonly_fields = ("created_by",)
    ordering = ("-start_at",)
    actions = ("publish_selected", "unpublish_selected")

    def save_model(
        self, request: HttpRequest, obj: Announcement, form: Any, change: bool
    ) -> None:
        if not change and not obj.created_by:
            obj.created_by = current_user(request)
        super().save_model(request, obj, form, change)

    @display(description=_("Created by"))
    def created_by_display(self, obj: Announcement) -> str:
        creator = obj.created_by
        return (creator.email or creator.username or "—") if creator else "—"

    @display(
        description=_("State"),
        ordering="is_active",
        label={"Live": "success", "Off": "danger"},
    )
    def state(self, obj: Announcement) -> str:
        return "Live" if obj.is_active else "Off"

    @action(description=_("Publish selected announcements"))
    def publish_selected(
        self, request: HttpRequest, queryset: QuerySet[Announcement]
    ) -> None:
        count = queryset.update(is_active=True)
        self.message_user(
            request, _("%(count)d announcements published.") % {"count": count}
        )

    @action(description=_("Unpublish selected announcements"))
    def unpublish_selected(
        self, request: HttpRequest, queryset: QuerySet[Announcement]
    ) -> None:
        count = queryset.update(is_active=False)
        self.message_user(
            request, _("%(count)d announcements unpublished.") % {"count": count}
        )


@admin.register(UsersSettings)
class UsersSettingsAdmin(SingletonModelAdmin):
    """Users app configuration."""

    fieldsets = (
        (_("Access & flows"), {"fields": ("enable_signup", "enable_password_reset")}),
        (
            _("Notifications"),
            {
                "fields": ("enable_notifications", "notification_stats"),
                "description": _(
                    "Master switch for all notifications. When disabled, no "
                    "notifications are sent."
                ),
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
        (_("Profile completion"), {"fields": ("required_profile_fields",)}),
        (_("Payments"), {"fields": ("enable_payments",)}),
    )
    readonly_fields = ("notification_stats",)

    @display(description=_("Notification system"))
    def notification_stats(self, obj: UsersSettings) -> str:
        total_users = CustomUser.objects.filter(is_active=True).count()
        prefs = NotificationPreferences.objects.count()
        push = PushSubscription.objects.filter(is_active=True).count()
        unread = Notification.objects.filter(is_read=False).count()
        share = (prefs / total_users * 100) if total_users else 0
        return format_html(
            "{} active users · {} with preferences ({:.1f}%) · "
            "{} active push subscriptions · {} unread notifications",
            total_users,
            prefs,
            share,
            push,
            unread,
        )


@admin.register(NotificationPreferences)
class NotificationPreferencesAdmin(ModelAdmin):
    list_display = (
        "user_email",
        "email_frequency",
        "push_enabled",
        "quiet_hours_enabled",
        "email_prefs_summary",
        "web_prefs_summary",
        "updated_at",
    )
    list_filter = (
        ("email_frequency", ChoicesDropdownFilter),
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
    ordering = ("-updated_at",)
    date_hierarchy = "updated_at"
    list_select_related = ("user",)
    fieldsets = (
        (_("User"), {"fields": ("user", "created_at", "updated_at")}),
        (
            _("Email notifications"),
            {
                "fields": (
                    "email_frequency",
                    "email_comments",
                    "email_replies",
                    "email_mentions",
                    "email_new_posts",
                    "email_security",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Web notifications"),
            {
                "fields": (
                    "web_comments",
                    "web_awards",
                    "web_moderation",
                    "web_system",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Push & quiet hours"),
            {
                "fields": (
                    "push_enabled",
                    "quiet_hours_enabled",
                    "quiet_hours_start",
                    "quiet_hours_end",
                ),
                "classes": ("tab",),
            },
        ),
    )
    conditional_fields: ClassVar[dict[str, str]] = {
        "quiet_hours_start": "quiet_hours_enabled == true",
        "quiet_hours_end": "quiet_hours_enabled == true",
    }
    actions = (
        "enable_all_email",
        "disable_all_email",
        "set_email_instant",
        "set_email_daily",
        "enable_push_all",
        "disable_push_all",
        "enable_quiet_hours",
        "disable_quiet_hours",
        "reset_to_defaults",
    )

    @display(description=_("User"), ordering="user__email")
    def user_email(self, obj: NotificationPreferences) -> str:
        return obj.user.email

    @display(description=_("Email preferences"))
    def email_prefs_summary(self, obj: NotificationPreferences) -> str:
        enabled = [
            label
            for label, flag in (
                ("comments", obj.email_comments),
                ("replies", obj.email_replies),
                ("mentions", obj.email_mentions),
                ("new posts", obj.email_new_posts),
            )
            if flag
        ]
        return ", ".join(enabled) or "None"

    @display(description=_("Web preferences"))
    def web_prefs_summary(self, obj: NotificationPreferences) -> str:
        enabled = [
            label
            for label, flag in (
                ("comments", obj.web_comments),
                ("awards", obj.web_awards),
                ("moderation", obj.web_moderation),
                ("system", obj.web_system),
            )
            if flag
        ]
        return ", ".join(enabled) or "None"

    def _bulk_update(
        self,
        request: HttpRequest,
        queryset: QuerySet[NotificationPreferences],
        message: str,
        **values: Any,
    ) -> None:
        try:
            updated = queryset.update(**values)
        except DatabaseError:
            logger.exception("Bulk notification preference update failed")
            self.message_user(request, _("Operation failed."), messages.ERROR)
            return
        self.message_user(request, message % {"count": updated}, messages.SUCCESS)

    @action(description=_("Enable all email notifications"))
    def enable_all_email(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Enabled all email notifications for %(count)d users.",
            email_comments=True,
            email_replies=True,
            email_mentions=True,
            email_new_posts=True,
        )

    @action(description=_("Disable all email notifications"))
    def disable_all_email(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Disabled all email notifications for %(count)d users.",
            email_comments=False,
            email_replies=False,
            email_mentions=False,
            email_new_posts=False,
            email_frequency="never",
        )

    @action(description=_("Set email frequency to instant"))
    def set_email_instant(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Set instant email frequency for %(count)d users.",
            email_frequency="instant",
        )

    @action(description=_("Set email frequency to daily digest"))
    def set_email_daily(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Set daily digest for %(count)d users.",
            email_frequency="daily",
        )

    @action(description=_("Enable push notifications"))
    def enable_push_all(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Enabled push notifications for %(count)d users.",
            push_enabled=True,
        )

    @action(description=_("Disable push notifications"))
    def disable_push_all(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Disabled push notifications for %(count)d users.",
            push_enabled=False,
        )

    @action(description=_("Enable quiet hours (22:00-08:00)"))
    def enable_quiet_hours(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Enabled quiet hours for %(count)d users.",
            quiet_hours_enabled=True,
            quiet_hours_start="22:00",
            quiet_hours_end="08:00",
        )

    @action(description=_("Disable quiet hours"))
    def disable_quiet_hours(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Disabled quiet hours for %(count)d users.",
            quiet_hours_enabled=False,
        )

    @action(description=_("Reset preferences to defaults"))
    def reset_to_defaults(self, request: HttpRequest, queryset: QuerySet) -> None:
        self._bulk_update(
            request,
            queryset,
            "Reset preferences to defaults for %(count)d users.",
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


BROWSER_NAMES = ("Chrome", "Firefox", "Safari", "Edge", "Opera")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(ModelAdmin):
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
        ("created_at", RangeDateTimeFilter),
        ("last_used_at", RangeDateTimeFilter),
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
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    fieldsets = (
        (
            _("User & device"),
            {"fields": ("user", "device_name", "user_agent", "is_active")},
        ),
        (
            _("Subscription details"),
            {
                "fields": ("endpoint", "p256dh", "auth"),
                "classes": ("collapse",),
                "description": _("Web Push subscription technical details."),
            },
        ),
        (_("Timestamps"), {"fields": ("created_at", "last_used_at")}),
    )
    actions = (
        "activate_selected",
        "deactivate_selected",
        "test_push_notification",
        "cleanup_old_subscriptions",
    )

    @display(description=_("User"), ordering="user__email")
    def user_email(self, obj: PushSubscription) -> str:
        return obj.user.email

    @display(description=_("Device"))
    def device_name_display(self, obj: PushSubscription) -> str:
        return obj.device_name or "Unknown device"

    @display(description=_("Browser"))
    def browser_info(self, obj: PushSubscription) -> str:
        agent = obj.user_agent or ""
        return next((name for name in BROWSER_NAMES if name in agent), "Unknown")

    @display(description=_("Age"))
    def subscription_age(self, obj: PushSubscription) -> str:
        delta = timezone.now() - obj.created_at
        if delta.days == 0:
            return f"{delta.seconds // 3600}h"
        if delta.days < 30:
            return f"{delta.days}d"
        if delta.days < 365:
            return f"{delta.days // 30}mo"
        return f"{delta.days // 365}y"

    @action(description=_("Activate selected subscriptions"))
    def activate_selected(
        self, request: HttpRequest, queryset: QuerySet[PushSubscription]
    ) -> None:
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            _("Activated %(count)d subscriptions.") % {"count": updated},
            messages.SUCCESS,
        )

    @action(description=_("Deactivate selected subscriptions"))
    def deactivate_selected(
        self, request: HttpRequest, queryset: QuerySet[PushSubscription]
    ) -> None:
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            _("Deactivated %(count)d subscriptions.") % {"count": updated},
            messages.SUCCESS,
        )

    @action(description=_("Send test push notification"))
    def test_push_notification(
        self, request: HttpRequest, queryset: QuerySet[PushSubscription]
    ) -> None:
        from apps.users.services.notifications import send_notification

        sent = 0
        for subscription in queryset.filter(is_active=True).select_related("user"):
            try:
                send_notification(
                    recipient=subscription.user,
                    title="Test notification from admin",
                    message=(
                        "This is a test push notification sent to "
                        f"{subscription.device_name or 'your device'}"
                    ),
                    action_type="system",
                    icon="bell",
                )
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Failed to send test notification: %s", exc)
                self.message_user(
                    request,
                    _("Could not notify %(user)s: %(error)s")
                    % {"user": subscription.user.email, "error": exc},
                    messages.WARNING,
                )
            else:
                sent += 1
        self.message_user(
            request,
            _("Sent test notifications to %(count)d subscriptions.") % {"count": sent},
            messages.SUCCESS if sent else messages.WARNING,
        )

    @action(description=_("Delete subscriptions inactive for 30+ days"))
    def cleanup_old_subscriptions(
        self, request: HttpRequest, queryset: QuerySet[PushSubscription]
    ) -> None:
        threshold = timezone.now() - timedelta(days=30)
        stale = queryset.filter(is_active=False, last_used_at__lt=threshold)
        count = stale.count()
        stale.delete()
        self.message_user(
            request,
            _("Deleted %(count)d old inactive subscriptions.") % {"count": count},
            messages.SUCCESS,
        )


# --------------------------------------------------------------------------
# Social authentication providers
# --------------------------------------------------------------------------
admin.site.unregister(SocialApp)


@admin.register(SocialApp)
class EnhancedSocialAppAdmin(BaseSocialAppAdmin, ModelAdmin):
    list_display = ("name", "provider", "status_display", "sites_list")
    list_filter = ("provider",)
    search_fields = ("name", "provider", "client_id")
    filter_horizontal = ("sites",)
    fieldsets = (
        (
            None,
            {
                "fields": ("provider", "name"),
                "description": _("Configure OAuth providers for social sign-in."),
            },
        ),
        (_("OAuth credentials"), {"fields": ("client_id", "secret", "key")}),
        (_("Configuration"), {"fields": ("sites", "settings")}),
    )

    @display(
        description=_("Status"),
        label={"Configured": "success", "Placeholder": "warning"},
    )
    def status_display(self, obj: SocialApp) -> str:
        if obj.client_id.startswith("your-") or len(obj.client_id) < 10:
            return "Placeholder"
        return "Configured"

    @display(description=_("Sites"))
    def sites_list(self, obj: SocialApp) -> str:
        domains = [site.domain for site in obj.sites.all()[:3]]
        return ", ".join(domains) or "No sites"

    def get_readonly_fields(
        self, request: HttpRequest, obj: SocialApp | None = None
    ) -> tuple[str, ...]:
        return ("provider",) if obj else ()


@admin.register(SecurityQuestion)
class SecurityQuestionAdmin(ModelAdmin):
    list_display = ("user", "question_key", "created_at")
    list_filter = (("question_key", ChoicesDropdownFilter), "created_at")
    search_fields = ("user__email", "user__username")
    readonly_fields = ("created_at",)
    raw_id_fields = ("user",)


@admin.register(MFADevice)
class MFADeviceAdmin(ModelAdmin):
    list_display = ("user", "device_type", "name", "is_active", "created_at")
    list_filter = (("device_type", ChoicesDropdownFilter), "is_active")
    search_fields = ("user__email", "user__username", "name")
    readonly_fields = ("created_at", "last_used_at")
    raw_id_fields = ("user",)
