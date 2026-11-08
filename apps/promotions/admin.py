"""Unfold admin for promotions: one object page per campaign with tabs and lifecycle actions."""

from __future__ import annotations

from typing import Any, ClassVar

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.functional import Promise
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from unfold.forms import BaseDialogForm

from apps.core.admin_base import (
    ModelAdmin,
    ReadOnlyModelAdmin,
    TabularInline,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)
from apps.users.request_user import current_user

from . import lifecycle
from .audience import audience_label
from .forms import ActivateForm, DuplicateForm, EndForm, ScheduleForm, UnscheduleForm
from .models import (
    Promotion,
    PromotionGrant,
    PromotionLog,
    PromotionProgress,
    PromotionStatus,
)

STATUS_BADGES = {
    "Draft": "info",
    "Scheduled": "warning",
    "Active": "success",
    "Ended": "danger",
}


class PromotionLogInline(TabularInline):
    """The audit trail of this promotion, newest first."""

    model = PromotionLog
    tab = True
    verbose_name_plural = _("Audit log")
    fields = ("created_at", "action", "actor", "user", "detail")
    readonly_fields = fields
    ordering = ("-created_at", "-id")
    max_num = 0
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@register(Promotion)
class PromotionAdmin(ModelAdmin):
    list_display = (
        "name",
        "kind",
        "status_badge",
        "window",
        "effect",
        "audience",
        "claims",
        "views",
    )
    list_filter = ("status", "kind", "audience")
    list_display_links = ("name",)
    search_fields = ("name", "slug", "headline", "description")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    readonly_fields = (
        "status",
        "offer_link",
        "announcement_link",
        "analytics",
        "activated_at",
        "ended_at",
        "created_by",
        "created_at",
        "updated_at",
    )
    inlines = (PromotionLogInline,)
    actions_row = ("schedule_promotion", "start_promotion", "end_promotion")
    actions_detail = (
        "schedule_promotion",
        "start_promotion",
        "end_promotion",
        "unschedule_promotion",
        "duplicate_promotion",
    )
    fieldsets = (
        (
            _("Offer"),
            {
                "fields": (
                    "name",
                    "slug",
                    "headline",
                    "description",
                    "terms",
                    "kind",
                    "audience",
                    ("status", "offer_link"),
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Schedule & banner"),
            {
                "fields": (
                    ("starts_at", "ends_at"),
                    "announce",
                    "cta_label",
                    "announcement_link",
                    ("activated_at", "ended_at"),
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Package & price"),
            {
                "fields": (
                    "target_package",
                    ("discounted_price", "discounted_credit_price"),
                    "trial_days",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Bonus & rewards"),
            {
                "fields": (
                    ("bonus_files", "bonus_size_gb", "bonus_devices"),
                    "bonus_credits",
                    "reward_multiplier",
                    "event_keys",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Earn rules"),
            {"fields": ("earn_rules",), "classes": ["tab"]},
        ),
        (
            _("Caps & budget"),
            {
                "fields": (
                    "max_claims_total",
                    "max_claims_per_user",
                    "budget_credits",
                ),
                "classes": ["tab"],
            },
        ),
        (
            _("Analytics"),
            {
                "fields": ("analytics", "created_by", "created_at", "updated_at"),
                "classes": ["tab"],
            },
        ),
    )

    def save_model(
        self, request: HttpRequest, obj: Promotion, form: Any, change: bool
    ) -> None:
        if not change and request.user.is_authenticated:
            obj.created_by = current_user(request)
        super().save_model(request, obj, form, change)

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: Promotion) -> str:
        return str(obj.get_status_display())

    @display(description=_("Window"))
    def window(self, obj: Promotion) -> str:
        start = f"{obj.starts_at:%d %b %Y}" if obj.starts_at else "on start"
        end = f"{obj.ends_at:%d %b %Y}" if obj.ends_at else "open"
        return f"{start} → {end}"

    @display(description=_("What it gives"))
    def effect(self, obj: Promotion) -> str:
        return obj.effect_summary()

    @display(description=_("Claims"), ordering="claim_count")
    def claims(self, obj: Promotion) -> int:
        return obj.claim_count

    @display(description=_("Views"), ordering="page_views")
    def views(self, obj: Promotion) -> int:
        return obj.page_views + obj.banner_views

    @display(description=_("Offer page"))
    def offer_link(self, obj: Promotion | None) -> SafeString | str:
        if obj is None or obj.pk is None:
            return "—"
        return format_html('<a href="{}">{}</a>', obj.get_absolute_url(), obj.slug)

    @display(description=_("Announcement"))
    def announcement_link(self, obj: Promotion | None) -> SafeString | str:
        if obj is None or obj.announcement_id is None:
            return str(_("Created when the promotion starts."))
        url = reverse("admin:users_announcement_change", args=[obj.announcement_id])
        return format_html('<a href="{}">{}</a>', url, obj.announcement)

    @display(description=_("Performance"))
    def analytics(self, obj: Promotion | None) -> SafeString | str:
        if obj is None or obj.pk is None:
            return str(_("Save the promotion to see its numbers."))
        rows = [
            (_("Offer page views"), obj.page_views),
            (_("Banner impressions"), obj.banner_views),
            (_("Clicks from banner and notifications"), obj.announcement_clicks),
            (_("Claims and rewards granted"), obj.claim_count),
            (
                _("Conversions (discounted orders, boosted rewards)"),
                obj.conversion_count,
            ),
            (_("Credits handed out"), obj.credits_granted),
            (_("Audience"), audience_label(obj)),
        ]
        grants = reverse("admin:promotions_promotiongrant_changelist")
        table = format_html_join(
            "", '<p class="mb-1"><strong>{}:</strong> {}</p>', rows
        )
        return format_html(
            '{}<p class="mt-2"><a href="{}?promotion__id__exact={}">{}</a></p>',
            table,
            grants,
            obj.pk,
            _("View grants"),
        )

    def _run(
        self,
        request: HttpRequest,
        object_id: str,
        change: Any,
        done: str | Promise,
    ) -> HttpResponse:
        promotion = self.get_object(request, unquote(object_id))
        if promotion is None:
            raise Http404
        try:
            change(promotion, actor=request.user)
        except (lifecycle.LifecycleError, ValidationError) as error:
            detail = (
                "; ".join(error.messages)
                if isinstance(error, ValidationError)
                else str(error)
            )
            self.message_user(request, f"{promotion.name}: {detail}", messages.ERROR)
        else:
            self.message_user(request, done)
        return htmx_redirect(return_url(request, change_url(self, promotion.pk)))

    @action(
        description=_("Schedule"),
        icon="schedule",
        permissions=["change"],
        url_path="schedule-promotion",
        dialog=confirm_dialog(
            _("Schedule this promotion"),
            _("It starts at its start time (or right away) and ends at its end time."),
            ScheduleForm,
            submit=_("Schedule"),
        ),
    )
    def schedule_promotion(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(request, object_id, lifecycle.schedule, _("Scheduled."))

    @action(
        description=_("Start now"),
        icon="play_arrow",
        permissions=["change"],
        url_path="start-promotion",
        dialog=confirm_dialog(
            _("Start this promotion now"),
            _("Publishes its banner and notifies its audience immediately."),
            ActivateForm,
            submit=_("Start now"),
        ),
    )
    def start_promotion(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(request, object_id, lifecycle.activate_now, _("Started."))

    @action(
        description=_("End now"),
        icon="stop_circle",
        permissions=["change"],
        url_path="end-promotion",
        dialog=confirm_dialog(
            _("End this promotion now"),
            _(
                "The banner expires and the offer stops. Grants without a trial "
                "length end with it."
            ),
            EndForm,
            submit=_("End now"),
        ),
    )
    def end_promotion(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(request, object_id, lifecycle.end_now, _("Ended."))

    @action(
        description=_("Back to draft"),
        icon="edit_note",
        permissions=["change"],
        url_path="unschedule-promotion",
        dialog=confirm_dialog(
            _("Return to draft"),
            _("Stops the schedule so you can edit the promotion."),
            UnscheduleForm,
            submit=_("Back to draft"),
        ),
    )
    def unschedule_promotion(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        return self._run(
            request, object_id, lifecycle.return_to_draft, _("Back to draft.")
        )

    def has_duplicate_permission(
        self, request: HttpRequest, _object_id: str | None = None
    ) -> bool:
        return self.has_add_permission(request)

    @action(
        description=_("Duplicate"),
        icon="content_copy",
        permissions=["duplicate"],
        url_path="duplicate-promotion",
        dialog=confirm_dialog(
            _("Duplicate this promotion"),
            _("Creates a draft copy without counters, grants or announcement."),
            DuplicateForm,
            submit=_("Duplicate"),
        ),
    )
    def duplicate_promotion(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        promotion = self.get_object(request, unquote(object_id))
        if promotion is None:
            raise Http404
        copy = lifecycle.duplicate(promotion, actor=request.user)
        self.message_user(request, _("Created the draft “%(name)s”.") % {"name": copy})
        return htmx_redirect(change_url(self, copy.pk))

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        if obj is not None and obj.status in (
            PromotionStatus.ACTIVE,
            PromotionStatus.SCHEDULED,
        ):
            return False
        return super().has_delete_permission(request, obj)


@register(PromotionGrant)
class PromotionGrantAdmin(ReadOnlyModelAdmin):
    list_display = (
        "promotion",
        "user",
        "trigger",
        "outcome",
        "credits",
        "granted_at",
        "expires_at",
    )
    list_filter = ("outcome", "promotion")
    search_fields = ("user__email", "user__username", "trigger")
    list_select_related = ("promotion", "user")


@register(PromotionProgress)
class PromotionProgressAdmin(ReadOnlyModelAdmin):
    list_display = ("promotion", "user", "event_key", "ref", "counted_at")
    list_filter = ("promotion", "event_key")
    search_fields = ("user__email", "user__username", "ref")
    list_select_related = ("promotion", "user")


@register(PromotionLog)
class PromotionLogAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "promotion", "action", "actor", "user", "detail")
    list_filter = ("action", "promotion")
    search_fields = ("detail", "user__email")
    list_select_related = ("promotion", "actor", "user")
