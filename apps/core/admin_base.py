"""Shared Unfold base classes for every installed app's admin.

All ``ModelAdmin``/inline classes in ``apps/*/admin.py`` inherit from here so the
operations console has one set of defaults (pagination, save bar, unsaved-form
warning, cheap result counts) and one place to change them. Apps import from this
module instead of ``unfold`` or ``django.contrib.admin`` directly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.options import BaseModelAdmin
from django.db.models import Model
from django.http import HttpRequest, HttpResponse
from django.template.response import SimpleTemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
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

from apps.core.utils.managers import default_manager

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
    "RelatedLink",
    "RelatedOverviewMixin",
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


@dataclass(frozen=True, slots=True)
class RelatedLink:
    """Records of another app that belong to the object being edited."""

    model: str  # "app_label.ModelName" of the related records
    lookup: str  # field path from the related model to the edited object
    label: str = ""  # defaults to the related model's plural name


_current_request: ContextVar[HttpRequest | None] = ContextVar(
    "admin_related_overview_request", default=None
)


def _forget_request(_response: object) -> None:
    _current_request.set(None)


class RelatedOverviewMixin(admin.ModelAdmin):
    """Adds a read-only "related records" field listing counts and filtered links.

    Objects such as an account or a package own work in several apps. Instead of
    importing those apps' models (or scattering the lookups across their changelists
    by hand), declare ``related_links`` and add ``related_overview`` to a tab. Only
    models that are registered in the admin and viewable by the current staff user
    are listed, so the page never leaks counts the user could not open.
    """

    related_links: ClassVar[Sequence[RelatedLink]] = ()

    def changeform_view(
        self,
        request: HttpRequest,
        object_id: str | None = None,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        # Read-only fields render after this method returns, so the request is kept
        # until the response has been rendered.
        _current_request.set(request)
        try:
            response = super().changeform_view(
                request, object_id, form_url, extra_context
            )
        except BaseException:
            _current_request.set(None)
            raise
        if isinstance(response, SimpleTemplateResponse) and not response.is_rendered:
            response.add_post_render_callback(_forget_request)
        else:
            _current_request.set(None)
        return response

    @display(description=_("Related records"))
    def related_overview(self, obj: Model | None) -> SafeString | str:
        request = _current_request.get()
        if request is None or obj is None or obj.pk is None:
            return "—"
        rows: list[tuple[str, object, object]] = []
        for link in self.related_links:
            related = django_apps.get_model(link.model)
            if not admin.site.is_registered(related):
                continue
            if not admin.site.get_model_admin(related).has_view_permission(request):
                continue
            opts = related._meta
            url = reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
            count = default_manager(related).filter(**{link.lookup: obj.pk}).count()
            rows.append(
                (
                    f"{url}?{link.lookup}__id__exact={obj.pk}",
                    link.label or opts.verbose_name_plural,
                    count,
                )
            )
        if not rows:
            return str(_("Nothing related that you can view."))
        return format_html(
            '<ul class="flex flex-col gap-2">{}</ul>',
            format_html_join(
                "",
                '<li><a class="text-primary-600 underline dark:text-primary-500" '
                'href="{}">{}</a> <span class="text-subtle tabular-nums">{}</span></li>',
                rows,
            ),
        )


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
