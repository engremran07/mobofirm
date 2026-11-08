"""Gamification administration (Unfold)."""

from __future__ import annotations

from typing import ClassVar

from django.contrib.admin import register
from django.db.models import Count, QuerySet
from django.forms import ModelForm
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    ReadOnlyModelAdmin,
    display,
)
from apps.gamification import services
from apps.gamification.models import (
    Badge,
    Level,
    Streak,
    UserBadge,
    XPRule,
    XPTransaction,
)

TIER_BADGES = {"bronze": "warning", "silver": "info", "gold": "success"}


@register(Level)
class LevelAdmin(ModelAdmin):
    list_display = ("number", "name", "min_xp")
    list_editable = ("name", "min_xp")
    ordering = ("number",)


@register(XPRule)
class XPRuleAdmin(NoAddModelAdmin):
    """What each event is worth. Events are declared in code, so no add/delete."""

    list_display = ("label", "event_key", "xp", "daily_cap", "is_enabled")
    list_editable = ("xp", "daily_cap", "is_enabled")
    fields = ("label", "event_key", "xp", "daily_cap", "is_enabled")
    readonly_fields = ("label", "event_key")

    def has_delete_permission(
        self, request: HttpRequest, obj: XPRule | None = None
    ) -> bool:
        return False


@register(Badge)
class BadgeAdmin(ModelAdmin):
    list_display = (
        "name",
        "tier_badge",
        "condition_display",
        "xp_bonus",
        "credits",
        "holders",
        "is_active",
    )
    list_filter = (("tier", ChoicesDropdownFilter), "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields: ClassVar[dict[str, tuple[str, ...]]] = {"slug": ("name",)}
    fieldsets = (
        (
            _("Badge"),
            {
                "fields": ("name", "slug", "description", "icon", "tier"),
                "classes": ["tab"],
            },
        ),
        (
            _("Condition"),
            {"fields": ("metric", "threshold"), "classes": ["tab"]},
        ),
        (
            _("Rewards"),
            {"fields": ("xp_bonus", "credits"), "classes": ["tab"]},
        ),
        (
            _("Status"),
            {"fields": ("is_active", "sort_order"), "classes": ["tab"]},
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Badge]:
        return super().get_queryset(request).annotate(held=Count("awards"))

    @display(description=_("Tier"), label=TIER_BADGES, ordering="tier")
    def tier_badge(self, obj: Badge) -> str:
        return obj.tier

    @display(description=_("Earned when"))
    def condition_display(self, obj: Badge) -> str:
        return obj.condition

    @display(description=_("Holders"), ordering="held")
    def holders(self, obj: Badge) -> int:
        return int(getattr(obj, "held", 0))


@register(UserBadge)
class UserBadgeAdmin(ModelAdmin):
    """Badges are earned automatically; staff can also award one by hand."""

    list_display = ("user", "badge", "awarded_manually", "earned_at")
    list_filter = (("earned_at", RangeDateTimeFilter), "awarded_manually")
    search_fields = ("user__username", "user__email", "badge__name")
    raw_id_fields = ("user",)
    list_select_related = ("user", "badge")
    fields = ("user", "badge")

    def get_readonly_fields(
        self, request: HttpRequest, obj: UserBadge | None = None
    ) -> tuple[str, ...]:
        return ("user", "badge") if obj is not None else ()

    def save_model(
        self,
        request: HttpRequest,
        obj: UserBadge,
        form: ModelForm,
        change: bool,
    ) -> None:
        if change:
            return
        # Goes through the service so the XP bonus, credits and notification happen.
        if services.grant_badge(obj.user, obj.badge):
            obj.pk = UserBadge.objects.get(user=obj.user, badge=obj.badge).pk


@register(XPTransaction)
class XPTransactionAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "user", "event_key", "xp", "reference")
    list_filter = ("event_key", ("created_at", RangeDateTimeFilter))
    search_fields = ("user__username", "user__email", "key", "reference")
    list_select_related = ("user",)


@register(Streak)
class StreakAdmin(ReadOnlyModelAdmin):
    list_display = ("user", "count", "longest", "last_active")
    search_fields = ("user__username", "user__email")
    list_select_related = ("user",)
