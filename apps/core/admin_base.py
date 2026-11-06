"""Shared Unfold base classes for every installed app's admin.

All ``ModelAdmin``/inline classes in ``apps/*/admin.py`` inherit from here so the
operations console has one set of defaults (pagination, save bar, unsaved-form
warning, cheap result counts) and one place to change them. Apps import from this
module instead of ``unfold`` or ``django.contrib.admin`` directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from django import forms
from django.contrib.admin.options import BaseModelAdmin
from django.db.models import Model
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from solo.admin import SingletonModelAdmin as SoloSingletonModelAdmin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from unfold.admin import StackedInline as UnfoldStackedInline
from unfold.admin import TabularInline as UnfoldTabularInline
from unfold.dataclasses import ActionDialog
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextareaWidget

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

__all__ = [
    "BADGE_APPROVAL",
    "BADGE_BOOLEAN",
    "BADGE_JOB",
    "ConfirmForm",
    "ModelAdmin",
    "NoAddModelAdmin",
    "ReadOnlyModelAdmin",
    "ReasonForm",
    "SingletonModelAdmin",
    "StackedInline",
    "TabularInline",
    "action",
    "change_url",
    "changelist_url",
    "confirm_dialog",
    "display",
    "htmx_redirect",
    "image_preview",
    "return_url",
]

# Unfold ``@display(label=...)`` colour variants per status value.
BADGE_APPROVAL: Mapping[str, str] = {
    "pending": "warning",
    "approved": "success",
    "rejected": "danger",
    "spam": "danger",
    "withdrawn": "info",
}
BADGE_JOB: Mapping[str, str] = {
    "queued": "info",
    "pending": "info",
    "running": "warning",
    "succeeded": "success",
    "completed": "success",
    "skipped": "info",
    "failed": "danger",
}
BADGE_BOOLEAN: Mapping[bool, str] = {True: "success", False: "danger"}


class ModelAdmin(UnfoldModelAdmin):
    """Defaults for list/detail pages that stay fast on large tables."""

    list_per_page = 50
    list_max_show_all = 200
    save_on_top = True
    show_full_result_count = False
    warn_unsaved_form = True
    list_filter_submit = True


class NoAddModelAdmin(ModelAdmin):
    """Records created by workflows or programmatic events, never by hand."""

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False


class ReadOnlyModelAdmin(NoAddModelAdmin):
    """Audit and telemetry tables: browsable, never edited or deleted in the UI."""

    def has_change_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: Model | None = None
    ) -> bool:
        return False


class SingletonModelAdmin(SoloSingletonModelAdmin, ModelAdmin):
    """django-solo singleton rendered with Unfold's own change form.

    solo's stock templates replace Unfold's header tools with legacy markup, so the
    Unfold defaults are used instead.
    """

    # Explicit Django defaults: solo's stock templates would replace Unfold's header tools.
    change_form_template = "admin/change_form.html"
    object_history_template = "admin/object_history.html"


class TabularInline(UnfoldTabularInline):
    extra = 0
    per_page = 25


class StackedInline(UnfoldStackedInline):
    extra = 0
    per_page = 25


def image_preview(url: str | None, alt: str = "") -> SafeString | str:
    """Thumbnail for read-only preview fields (no inline style: admin CSP)."""
    if not url:
        return "—"
    return format_html(
        '<img src="{}" alt="{}" class="h-16 w-16 rounded-default object-cover '
        'border border-base-200">',
        url,
        alt,
    )


class ConfirmForm(BaseDialogForm):
    """Confirmation-only dialog: no fields, just the CSRF-protected POST."""


class ReasonForm(BaseDialogForm):
    """Confirmation dialog that records why a decision was made."""

    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
    )


def confirm_dialog(
    title: StrOrPromise,
    description: StrOrPromise = "",
    form_class: type[BaseDialogForm] = ConfirmForm,
    submit: StrOrPromise = "Confirm",
) -> ActionDialog:
    """Dialog options for a state-changing row/detail/list action.

    Unfold renders row and detail actions as links, so anything that writes must
    use a dialog: the link only opens the confirmation form and the change happens
    on its CSRF-protected POST.
    """
    return {
        "title": title,
        "description": description,
        "form_class": form_class,
        "form_submit_text": submit,
    }


def htmx_redirect(url: str) -> HttpResponse:
    """Finish a dialog action: HTMX follows ``HX-Redirect`` after the POST."""
    return HttpResponse(headers={"HX-Redirect": url})


def return_url(request: HttpRequest, fallback: str) -> str:
    """Page the dialog was opened from (same host only), else ``fallback``."""
    candidate = request.headers.get("HX-Current-URL", "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def changelist_url(model_admin: BaseModelAdmin) -> str:
    opts = model_admin.opts
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")


def change_url(model_admin: BaseModelAdmin, object_id: object) -> str:
    opts = model_admin.opts
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[object_id])
