"""Referral administration (Unfold): settings, codes and the invited-user ledger.

Reward amounts are not edited here: they are the wallet's ``referral.verified`` and
``referral.welcome`` reward rules (Credits economy > Reward rules).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin import register
from django.db.models import Model as DjangoModel
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter

from apps.core.admin_base import (
    ModelAdmin,
    NoAddModelAdmin,
    SingletonModelAdmin,
    action,
    display,
)

from . import services
from .models import Referral, ReferralCode, ReferralSettings

STATUS_BADGES = {"pending": "warning", "rewarded": "success", "rejected": "danger"}


@register(ReferralSettings)
class ReferralSettingsAdmin(SingletonModelAdmin):
    fieldsets = (
        (
            _("Eligibility"),
            {
                "classes": ["tab"],
                "fields": ("enabled", "require_qualifying_action", "capture_days"),
            },
        ),
        (
            _("Anti-abuse"),
            {
                "classes": ["tab"],
                "fields": (
                    "max_rewarded_per_referrer",
                    "max_rewarded_per_referrer_per_day",
                    "block_same_network",
                ),
            },
        ),
    )


@register(ReferralCode)
class ReferralCodeAdmin(ModelAdmin):
    list_display = ("code", "user", "is_active", "clicks", "created_at")
    list_filter = ("is_active", ("created_at", RangeDateTimeFilter))
    search_fields = ("code", "user__email", "user__username")
    list_select_related = ("user",)
    readonly_fields = ("user", "code", "clicks", "network_hash", "created_at")
    fields = ("user", "code", "is_active", "clicks", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False


@register(Referral)
class ReferralAdmin(NoAddModelAdmin):
    list_display = (
        "invitee",
        "referrer",
        "status_badge",
        "reject_reason",
        "reward_credits",
        "created_at",
    )
    list_filter = (
        ("status", ChoicesDropdownFilter),
        "reject_reason",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("invitee__email", "referrer__email", "code")
    list_select_related = ("invitee", "referrer")
    readonly_fields = (
        "referrer",
        "invitee",
        "code",
        "status",
        "reject_reason",
        "email_verified_at",
        "qualified_at",
        "qualifying_kind",
        "reward_credits",
        "rewarded_at",
        "created_at",
    )
    fields = readonly_fields
    actions = ("reevaluate",)

    def has_delete_permission(
        self, request: HttpRequest, obj: DjangoModel | None = None
    ) -> bool:
        return False

    @display(description=_("Status"), ordering="status", label=STATUS_BADGES)
    def status_badge(self, obj: Referral) -> str:
        return obj.status

    @action(description=_("Re-check pending rewards"), permissions=["change"])
    def reevaluate(self, request: HttpRequest, queryset: QuerySet[Referral]) -> None:
        paid = 0
        for referral in queryset.filter(status=Referral.Status.PENDING):
            result = services.evaluate_reward(referral.invitee)
            paid += int(
                result is not None and result.status == Referral.Status.REWARDED
            )
        self.message_user(
            request, _("%(count)d reward(s) paid.") % {"count": paid}, messages.SUCCESS
        )
