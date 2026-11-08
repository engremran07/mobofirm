"""Consent administration (Unfold): categories, policy versions, audit log, stats."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.admin import register
from django.contrib.admin.utils import unquote
from django.core.exceptions import PermissionDenied
from django.forms import ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import ChoicesDropdownFilter, RangeDateTimeFilter
from unfold.forms import BaseDialogForm

from apps.consent import services
from apps.consent.models import ConsentCategory, ConsentDecision, ConsentPolicy
from apps.core.admin_base import ConfirmForm as PublishForm
from apps.core.admin_base import (
    ModelAdmin,
    ReadOnlyModelAdmin,
    action,
    change_url,
    confirm_dialog,
    display,
    htmx_redirect,
    return_url,
)

ACTION_BADGES = {
    "accept_all": "success",
    "reject_all": "danger",
    "custom": "info",
}


@register(ConsentCategory)
class ConsentCategoryAdmin(ModelAdmin):
    list_display = ("name", "slug", "required_badge", "is_active", "sort_order")
    list_editable = ("is_active", "sort_order")
    search_fields = ("slug", "name")
    fields = ("slug", "name", "description", "is_active", "sort_order")

    def get_readonly_fields(
        self, request: HttpRequest, obj: ConsentCategory | None = None
    ) -> tuple[str, ...]:
        return ("slug",) if obj is not None else ()

    def has_delete_permission(
        self, request: HttpRequest, obj: ConsentCategory | None = None
    ) -> bool:
        if obj is not None and obj.is_builtin:
            return False
        return super().has_delete_permission(request, obj)

    @display(description=_("Always on"), boolean=True)
    def required_badge(self, obj: ConsentCategory) -> bool:
        return obj.is_required


@register(ConsentPolicy)
class ConsentPolicyAdmin(ModelAdmin):
    list_display = ("version", "title", "state", "published_at", "decision_count")
    ordering = ("-version",)
    actions_row = ("publish_policy",)
    actions_detail = ("publish_policy",)
    readonly_fields = ("version", "published_at", "categories_summary", "is_active")

    def get_fields(
        self, request: HttpRequest, obj: ConsentPolicy | None = None
    ) -> list[str]:
        base = ["title", "banner_text", "details_text", "privacy_url"]
        if obj is None:
            return base
        return ["version", *base, "categories_summary", "is_active", "published_at"]

    def get_readonly_fields(
        self, request: HttpRequest, obj: ConsentPolicy | None = None
    ) -> tuple[str, ...]:
        locked = ("title", "banner_text", "details_text", "privacy_url")
        base = tuple(self.readonly_fields)
        return (*base, *locked) if obj is not None and obj.is_published else base

    def get_changeform_initial_data(self, request: HttpRequest) -> dict[str, str]:
        active = ConsentPolicy.objects.filter(is_active=True).first()
        if active is None:
            return {}
        return {
            "title": active.title,
            "banner_text": active.banner_text,
            "details_text": active.details_text,
            "privacy_url": active.privacy_url,
        }

    def save_model(
        self,
        request: HttpRequest,
        obj: ConsentPolicy,
        form: ModelForm,
        change: bool,
    ) -> None:
        if not change:
            obj.version = services.next_version()
        super().save_model(request, obj, form, change)

    def has_delete_permission(
        self, request: HttpRequest, obj: ConsentPolicy | None = None
    ) -> bool:
        if obj is not None and obj.is_published:
            return False
        return super().has_delete_permission(request, obj)

    @display(
        description=_("State"),
        label={"Active": "success", "Draft": "warning", "Superseded": "info"},
    )
    def state(self, obj: ConsentPolicy) -> str:
        if obj.is_active:
            return "Active"
        return "Draft" if not obj.is_published else "Superseded"

    @display(description=_("Records"))
    def decision_count(self, obj: ConsentPolicy) -> int:
        return obj.decisions.count()

    @display(description=_("Categories offered"))
    def categories_summary(self, obj: ConsentPolicy | None) -> str:
        if obj is None or not obj.categories_snapshot:
            return str(_("Captured when the version is published."))
        return ", ".join(str(item["name"]) for item in obj.categories_snapshot)

    @action(
        description=_("Publish this version"),
        icon="publish",
        permissions=["change"],
        url_path="publish-policy",
        dialog=confirm_dialog(
            _("Publish this consent policy"),
            _(
                "Every visitor is asked again, and the text and categories are "
                "frozen so the audit trail stays accurate."
            ),
            PublishForm,
            submit=_("Publish"),
        ),
    )
    def publish_policy(
        self, request: HttpRequest, _form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        policy = self.get_object(request, unquote(object_id))
        if policy is None:
            raise Http404
        if policy.is_active:
            self.message_user(
                request, _("This version is already active."), messages.WARNING
            )
        else:
            services.publish_policy(policy)
            self.message_user(
                request, _("Policy version %(v)d is live.") % {"v": policy.version}
            )
        return htmx_redirect(return_url(request, change_url(self, policy.pk)))


@register(ConsentDecision)
class ConsentDecisionAdmin(ReadOnlyModelAdmin):
    list_display = ("created_at", "action_badge", "granted_display", "policy", "source")
    list_filter = (
        ("action", ChoicesDropdownFilter),
        ("source", ChoicesDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("subject",)
    list_select_related = ("policy",)
    ordering = ("-created_at",)
    actions_list = ("open_stats",)

    def get_urls(self) -> list[URLPattern]:
        return [
            path(
                "stats/",
                self.admin_site.admin_view(self.stats_view),
                name="consent_consentdecision_stats",
            ),
            *super().get_urls(),
        ]

    def stats_view(self, request: HttpRequest) -> HttpResponse:
        if not self.has_view_permission(request):
            raise PermissionDenied
        context: dict[str, Any] = {
            **self.admin_site.each_context(request),
            "title": _("Consent statistics"),
            **services.consent_stats(),
        }
        return TemplateResponse(request, "admin/consent/stats.html", context)

    @action(description=_("Statistics"), url_path="open-stats", icon="bar_chart")
    def open_stats(self, request: HttpRequest) -> HttpResponse:
        return redirect("admin:consent_consentdecision_stats")

    @display(description=_("Choice"), label=ACTION_BADGES)
    def action_badge(self, obj: ConsentDecision) -> str:
        return obj.action

    @display(description=_("Allowed"))
    def granted_display(self, obj: ConsentDecision) -> str:
        return ", ".join(obj.granted) or "—"


__all__ = [
    "ConsentCategoryAdmin",
    "ConsentDecisionAdmin",
    "ConsentPolicyAdmin",
]
