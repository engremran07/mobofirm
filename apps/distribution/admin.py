"""Distribution administration (Unfold): channels, campaigns, queue, links, log.

The staff pages (dashboard, calendar, approvals, analytics, platform picker, preview) are
in ``staff_views`` and mounted on the channel admin so their URLs live under
``admin:distribution_channel_*``.
"""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import URLPattern, path, reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ConfirmForm,
    ModelAdmin,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    SingletonModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.distribution import channels, services, staff_views
from apps.distribution.forms import ChannelAdminForm, channel_form_for, cred_name
from apps.distribution.models import (
    Campaign,
    Channel,
    DistributionSettings,
    MediaAsset,
    ShareJob,
    ShareLog,
    ShortLink,
)

STATE_BADGES = {
    "off": "info",
    "ready": "success",
    "failing": "danger",
    "misconfigured": "warning",
}
JOB_BADGES = {
    "queued": "info",
    "pending": "warning",
    "needs_media": "warning",
    "sent": "success",
    "failed": "danger",
    "skipped": "warning",
    "cancelled": "info",
    "rejected": "danger",
}


class RecentJobInline(TabularInline):
    model = ShareJob
    fields = ("created_at", "content_kind", "title", "status", "attempts", "last_error")
    readonly_fields = fields
    can_delete = False
    max_num = 0
    per_page = 10
    ordering = ("-created_at",)

    def has_add_permission(
        self, request: HttpRequest, obj: Channel | None = None
    ) -> bool:
        return False


@register(Channel)
class ChannelAdmin(ModelAdmin):
    """Everything about one destination on one page: connection, copy, schedule."""

    form = ChannelAdminForm
    list_display = (
        "name",
        "platform",
        "state_badge",
        "approval_mode",
        "connection_badge",
        "last_success_at",
        "last_error",
    )
    list_filter = (("kind", ChoicesDropdownFilter), "enabled", "approval_mode")
    search_fields = ("name",)
    inlines = (RecentJobInline,)
    actions_row = ("test_message", "check_credentials", "open_preview")
    actions_detail = ("test_message", "check_credentials", "open_preview")
    actions_list = ("open_dashboard", "add_platform")
    readonly_fields = (
        "platform_summary",
        "status_summary",
        "connection_summary",
        "consecutive_failures",
        "last_success_at",
        "last_error",
        "disabled_reason",
    )

    # ------------------------------------------------------------ forms ----

    def _kind(self, request: HttpRequest, obj: Channel | None) -> str:
        if obj is not None and obj.kind:
            return obj.kind
        return str(request.POST.get("kind") or request.GET.get("kind") or "")

    def get_form(
        self,
        request: HttpRequest,
        obj: Channel | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm[Channel]]:
        kwargs["form"] = channel_form_for(self._kind(request, obj))
        return super().get_form(request, obj, change=change, **kwargs)

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, Any]:
        initial = super().get_changeform_initial_data(request)
        kind = request.GET.get("kind", "")
        adapter = channels.get_adapter(kind)
        if adapter is not None:
            initial.setdefault("kind", kind)
            initial.setdefault("name", adapter.label)
            initial.setdefault("send_windows", "")
        return initial

    def get_readonly_fields(
        self, request: HttpRequest, obj: Channel | None = None
    ) -> tuple[str, ...]:
        fixed = tuple(super().get_readonly_fields(request, obj))
        return (*fixed, "kind") if obj is not None else fixed

    def get_fieldsets(
        self, request: HttpRequest, obj: Channel | None = None
    ) -> list[tuple[str | None, dict[str, Any]]]:
        adapter = channels.get_adapter(self._kind(request, obj))
        creds = (
            tuple(cred_name(spec.name) for spec in adapter.fields) if adapter else ()
        )
        hint = (
            f"{adapter.how_to} {adapter.docs_url}".strip()
            if adapter and (adapter.how_to or adapter.docs_url)
            else ""
        )
        connection: dict[str, Any] = {
            "fields": ("name", "kind", "enabled", *creds, "clear_secret"),
            "classes": ["tab"],
        }
        if hint:
            connection["description"] = hint
        return [
            (str(_("Connection")), connection),
            (
                str(_("What to share")),
                {
                    "fields": (
                        "share_firmware",
                        "share_blog_posts",
                        "use_composer",
                        "campaign",
                        "referral_user",
                        "firmware_template",
                        "blog_template",
                    ),
                    "classes": ["tab"],
                    "description": str(
                        _(
                            "With the post composer on, copy is written per platform "
                            "(hook, hashtags, tracked link). The two templates are used "
                            "only when it is off."
                        )
                    ),
                },
            ),
            (
                str(_("Schedule & approval")),
                {
                    "fields": (
                        "approval_mode",
                        "timezone",
                        "send_windows",
                        "quiet_start",
                        "quiet_end",
                        "delay_minutes",
                        "daily_limit",
                        "kind_daily_cap",
                        "max_attempts",
                    ),
                    "classes": ["tab"],
                },
            ),
            (
                str(_("Status")),
                {
                    "fields": (
                        "platform_summary",
                        "status_summary",
                        "connection_summary",
                        "consecutive_failures",
                        "last_success_at",
                        "last_error",
                        "disabled_reason",
                    ),
                    "classes": ["tab"],
                },
            ),
        ]

    # ------------------------------------------------------------- views ----

    def add_view(
        self,
        request: HttpRequest,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        if (
            request.method == "GET"
            and channels.get_adapter(request.GET.get("kind", "")) is None
        ):
            return staff_views.platforms(request)
        return super().add_view(request, form_url, extra_context)

    def get_urls(self) -> list[URLPattern]:
        wrap = self.admin_site.admin_view
        routes = (
            ("dashboard/", staff_views.dashboard, "dashboard"),
            ("run-queue/", staff_views.run_queue, "run_queue"),
            ("calendar/", staff_views.calendar, "calendar"),
            ("review/", staff_views.review, "review"),
            ("analytics/", staff_views.analytics_page, "analytics"),
            ("analytics/export/", staff_views.analytics_export, "analytics_export"),
            ("platforms/", staff_views.platforms, "platforms"),
            ("channel/<int:pk>/test/", staff_views.channel_test, "test_post"),
            ("channel/<int:pk>/check/", staff_views.channel_check, "check"),
            ("channel/<int:pk>/preview/", staff_views.preview, "preview"),
            ("job/<int:pk>/cancel/", staff_views.job_cancel, "job_cancel"),
            ("job/<int:pk>/reschedule/", staff_views.job_reschedule, "job_reschedule"),
            ("job/<int:pk>/decide/", staff_views.job_decide, "job_decide"),
        )
        custom = [
            path(route, wrap(view), name=f"distribution_channel_{name}")
            for route, view, name in routes
        ]
        return [*custom, *super().get_urls()]

    # ----------------------------------------------------------- display ----

    @action(description=_("Dashboard"), url_path="open-dashboard", icon="monitoring")
    def open_dashboard(self, request: HttpRequest) -> HttpResponse:
        return redirect("admin:distribution_channel_dashboard")

    @action(description=_("Add channel"), url_path="add-platform", icon="add")
    def add_platform(self, request: HttpRequest) -> HttpResponse:
        return redirect("admin:distribution_channel_platforms")

    @display(description=_("Platform"), ordering="kind")
    def platform(self, obj: Channel) -> str:
        adapter = obj.adapter
        return adapter.label if adapter else obj.kind

    @display(description=_("Status"), label=STATE_BADGES)
    def state_badge(self, obj: Channel) -> str:
        return obj.state

    @display(
        description=_("Connection"),
        label={"ok": "success", "failed": "danger", "untested": "info"},
    )
    def connection_badge(self, obj: Channel) -> str:
        if obj.connection_ok is None:
            return "untested"
        return "ok" if obj.connection_ok else "failed"

    @display(description=_("Platform requirements"))
    def platform_summary(self, obj: Channel | None) -> str:
        adapter = obj.adapter if obj is not None else None
        if adapter is None:
            return "—"
        parts = [
            f"Media: {adapter.media}"
            + (" (image optional)" if adapter.image_optional else ""),
            f"Limit: {adapter.message_limit} characters",
            f"Hashtags: up to {adapter.hashtag_limit}",
            f"Minimum gap: {adapter.min_interval // 60} min"
            if adapter.min_interval
            else "No minimum gap",
        ]
        if adapter.daily_max:
            parts.append(f"Platform ceiling: {adapter.daily_max} posts/day")
        return " · ".join(parts)

    @display(description=_("Current state"))
    def status_summary(self, obj: Channel | None) -> str:
        if obj is None or obj.pk is None:
            return str(_("Save the channel to see its status."))
        notes = {
            "off": "Off: nothing is posted until you enable it.",
            "misconfigured": obj.configuration_problem,
            "failing": f"Failing: {obj.consecutive_failures} posts in a row. {obj.last_error}",
            "ready": "Ready: new items are posted automatically.",
        }
        return notes[obj.state]

    @display(description=_("Last connection test"))
    def connection_summary(self, obj: Channel | None) -> str:
        if obj is None or obj.connection_checked_at is None:
            return str(_("Not tested yet. Use “Test connection”."))
        verdict = {True: "OK", False: "Failed", None: "Not available"}[
            obj.connection_ok
        ]
        return f"{verdict}: {obj.connection_message} ({obj.connection_checked_at:%Y-%m-%d %H:%M})"

    # ----------------------------------------------------------- actions ----

    @action(
        description=_("Send a test post"),
        icon="send",
        permissions=["change"],
        url_path="send-test",
        dialog=confirm_dialog(
            _("Send a test post"),
            _("Posts one clearly labelled test post to this channel now."),
            ConfirmForm,
            submit=_("Send test"),
        ),
    )
    def test_message(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        channel = self.get_object(request, unquote(object_id))
        if channel is None:
            raise Http404
        job = services.send_test(channel)
        if job.status == ShareJob.Status.SENT:
            self.message_user(request, _("Test post delivered."))
        else:
            self.message_user(
                request,
                _("Test failed: %(error)s") % {"error": job.last_error},
                messages.ERROR,
            )
        return htmx_redirect(return_url(request, change_url(self, channel.pk)))

    @action(
        description=_("Test connection"),
        icon="wifi_tethering",
        permissions=["change"],
        url_path="check-credentials",
        dialog=confirm_dialog(
            _("Test connection"),
            _("Checks the credentials with a read-only call. Nothing is posted."),
            ConfirmForm,
            submit=_("Test"),
        ),
    )
    def check_credentials(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        channel = self.get_object(request, unquote(object_id))
        if channel is None:
            raise Http404
        outcome = services.check_connection(channel)
        detail = outcome.detail or outcome.code
        level = messages.SUCCESS if outcome.ok else messages.WARNING
        self.message_user(request, f"{channel.name}: {detail}", level)
        return htmx_redirect(return_url(request, change_url(self, channel.pk)))

    @action(description=_("Preview post"), icon="visibility", url_path="open-preview")
    def open_preview(self, request: HttpRequest, object_id: str) -> HttpResponse:
        return redirect(reverse("admin:distribution_channel_preview", args=[object_id]))

    def save_model(
        self, request: HttpRequest, obj: Channel, form: Any, change: bool
    ) -> None:
        if obj.enabled:
            obj.disabled_reason = ""
        super().save_model(request, obj, form, change)


@register(Campaign)
class CampaignAdmin(ModelAdmin):
    """Reusable copy and schedule presets a channel can follow."""

    list_display = ("name", "hook_styles", "emoji_policy", "ab_test", "evergreen")
    search_fields = ("name",)
    fieldsets = (
        (
            _("Copy"),
            {
                "fields": (
                    "name",
                    "description",
                    "hook_styles",
                    "emoji_policy",
                    "hashtag_cap",
                    "generate_media",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Tracking"),
            {
                "fields": ("use_short_links", "utm_campaign", "use_referral"),
                "classes": ["tab"],
            },
        ),
        (
            _("Growth"),
            {
                "fields": (
                    "ab_test",
                    "ab_min_clicks",
                    "evergreen",
                    "evergreen_gap_days",
                    "evergreen_per_week",
                    "trending_boost",
                ),
                "classes": ["tab"],
            },
        ),
    )


@register(ShareJob)
class ShareJobAdmin(NoAddModelAdmin):
    """The queue. Jobs are created by events; staff can retry, cancel or approve."""

    list_display = (
        "created_at",
        "channel",
        "content_kind",
        "title",
        "status_badge",
        "next_attempt_at",
        "attempts",
        "last_error",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        "channel",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("title", "object_ref", "idempotency_key")
    list_select_related = ("channel",)
    actions = ("retry_selected", "cancel_selected", "approve_selected")
    readonly_fields = (
        "channel",
        "content_kind",
        "object_ref",
        "idempotency_key",
        "title",
        "url",
        "target_url",
        "text",
        "hook",
        "reshare_round",
        "trending",
        "status",
        "attempts",
        "next_attempt_at",
        "last_error",
        "response_code",
        "external_id",
        "decided_by",
        "decided_at",
        "decision_note",
        "created_at",
        "sent_at",
    )
    exclude = ("remote_state",)

    def has_delete_permission(
        self, request: HttpRequest, obj: ShareJob | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), label=JOB_BADGES, ordering="status")
    def status_badge(self, obj: ShareJob) -> str:
        return obj.status

    @action(description=_("Retry selected"), permissions=["change"])
    def retry_selected(
        self, request: HttpRequest, queryset: QuerySet[ShareJob]
    ) -> None:
        count = sum(
            services.retry_job(job) for job in queryset.select_related("channel")
        )
        self.message_user(request, _("%(n)d job(s) queued again.") % {"n": count})

    @action(description=_("Cancel selected"), permissions=["change"])
    def cancel_selected(
        self, request: HttpRequest, queryset: QuerySet[ShareJob]
    ) -> None:
        count = sum(
            services.cancel_job(job, request.user)
            for job in queryset.select_related("channel")
        )
        self.message_user(request, _("%(n)d job(s) cancelled.") % {"n": count})

    @action(description=_("Approve selected"), permissions=["change"])
    def approve_selected(
        self, request: HttpRequest, queryset: QuerySet[ShareJob]
    ) -> None:
        count = sum(
            services.approve_job(job, request.user)
            for job in queryset.select_related("channel")
        )
        self.message_user(request, _("%(n)d job(s) approved.") % {"n": count})


@register(ShareLog)
class ShareLogAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "channel", "level", "message", "response_code")
    list_filter = ("level", "channel", ("created_at", RangeDateTimeFilter))
    search_fields = ("message",)
    list_select_related = ("channel",)


@register(ShortLink)
class ShortLinkAdmin(ReadOnlyModelAdmin):
    list_display = (
        "code",
        "channel",
        "content_kind",
        "hook",
        "clicks",
        "bot_clicks",
        "created_at",
    )
    list_filter = ("channel", "content_kind", "hook")
    search_fields = ("code", "object_ref", "target_url")
    list_select_related = ("channel",)


@register(MediaAsset)
class MediaAssetAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "content_kind", "object_ref", "asset_kind", "reason")
    list_filter = ("asset_kind",)
    search_fields = ("object_ref",)


@register(DistributionSettings)
class DistributionSettingsAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            _("Retention"),
            {
                "fields": (
                    "click_retention_days",
                    "log_retention_days",
                    "media_retention_days",
                ),
                "classes": ["tab"],
            },
        ),
    )
