"""Shared base classes for every installed app's admin (Django admin only).

All ``ModelAdmin``/inline classes in ``apps/*/admin.py`` inherit from here so the
operations console has one set of defaults (pagination, save bar, unsaved-form
warning, cheap result counts) and one place to change them. Apps import from this
module instead of ``django.contrib.admin`` directly; this is the only seam between
the apps and the console implementation.

Public API (see ``admin_actions``, ``admin_forms`` and ``admin_filters`` for details)::

    ModelAdmin, NoAddModelAdmin, ReadOnlyModelAdmin, SingletonModelAdmin
    TabularInline, StackedInline          # extra=0, per_page=25, tab/collapsible flags
    RelatedLink, RelatedOverviewMixin
    action, display, ActionVariant        # decorators + variant enum
    ConfirmForm, ReasonForm, BaseDialogForm, confirm_dialog
    htmx_redirect, return_url, change_url, changelist_url, image_preview
    BADGE_APPROVAL, BADGE_JOB, BADGE_BOOLEAN
    ImportExportMixin, Resource, Column, RowError   # CSV/JSON(/XLSX) import and export
    (see ``admin_import_export``: dry-run preview, atomic apply, formula-safe export)

``ModelAdmin`` class attributes beyond Django's:

``actions_list`` / ``actions_row`` / ``actions_detail`` / ``actions_submit_line``
    Names of ``@action`` methods (see ``admin_actions``).
``warn_unsaved_form`` / ``list_filter_submit``
    Booleans exposed to the templates as ``console_warn_unsaved_form`` and
    ``console_list_filter_submit``.
``conditional_fields``
    ``{"target_field": "other_field == true"}``: show the target only while the
    condition holds. Clauses are ``<field> ==|!= true|false|'text'|number`` joined
    with ``&&``. Exposed to the change-form template as
    ``console_conditional_rules``: ``[{"field": target, "when": [{"field", "op"
    ("eq"|"ne"), "value"}, ...]}]`` (render it with ``json_script``; no expression
    is ever evaluated in the browser).
Inlines: ``per_page`` paginates the rows (``formset.paginator`` / ``formset.page``;
the page number travels as ``<prefix>-page``), ``tab = True`` asks the form template
to show the inline on its own tab, ``collapsible`` is a presentation hint.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from django import forms
from django.apps import apps as django_apps
from django.contrib import admin
from django.contrib.admin.options import BaseModelAdmin, InlineModelAdmin
from django.core import checks
from django.core.paginator import Page, Paginator
from django.db.models import Model
from django.forms import BaseInlineFormSet
from django.http import HttpRequest, HttpResponse
from django.template.response import SimpleTemplateResponse
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _
from solo.admin import SingletonModelAdmin as SoloSingletonModelAdmin

from apps.core.admin_actions import (
    ActionDialog,
    ActionVariant,
    ConsoleActionsMixin,
    action,
    display,
)
from apps.core.admin_forms import (
    ActionForm,
    BaseDialogForm,
    ConsoleTextareaWidget,
)
from apps.core.admin_import_export import (
    Column,
    ImportExportMixin,
    Resource,
    RowError,
    parse_bool,
    split_list,
)
from apps.core.admin_widgets import ConsoleAutocompleteMixin
from apps.core.utils.managers import default_manager

if TYPE_CHECKING:
    from django_stubs_ext import StrOrPromise

__all__ = [
    "BADGE_APPROVAL",
    "BADGE_BOOLEAN",
    "BADGE_JOB",
    "ActionVariant",
    "BaseDialogForm",
    "Column",
    "ConfirmForm",
    "ImportExportMixin",
    "ModelAdmin",
    "NoAddModelAdmin",
    "ReadOnlyModelAdmin",
    "ReasonForm",
    "RelatedLink",
    "RelatedOverviewMixin",
    "Resource",
    "RowError",
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
    "parse_bool",
    "parse_condition",
    "return_url",
    "split_list",
]

# ``@display(label=...)`` badge tones per status value (see ``BADGE_TONES``).
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


_CLAUSE = re.compile(
    r"""^\s*(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>==|!=)\s*
        (?P<value>true|false|'[^']*'|"[^"]*"|-?\d+(?:\.\d+)?)\s*$""",
    re.VERBOSE,
)


def parse_condition(expression: str) -> list[dict[str, Any]]:
    """Parse ``"a == true && b != 'x'"`` into ``[{"field", "op", "value"}, ...]``."""
    clauses: list[dict[str, Any]] = []
    for part in expression.split("&&"):
        match = _CLAUSE.match(part)
        if match is None:
            raise ValueError(f"Unsupported condition {part.strip()!r}.")
        raw = match["value"]
        value: Any
        if raw in {"true", "false"}:
            value = raw == "true"
        elif raw[0] in "'\"":
            value = raw[1:-1]
        else:
            value = float(raw) if "." in raw else int(raw)
        clauses.append(
            {
                "field": match["field"],
                "op": "eq" if match["op"] == "==" else "ne",
                "value": value,
            }
        )
    return clauses


class ModelAdmin(ConsoleAutocompleteMixin, ConsoleActionsMixin, admin.ModelAdmin):
    """Defaults for list/detail pages that stay fast on large tables."""

    list_per_page = 50
    list_max_show_all = 200
    save_on_top = True
    show_full_result_count = False
    warn_unsaved_form = True
    list_filter_submit = True
    action_form = ActionForm
    conditional_fields: ClassVar[Mapping[str, str]] = {}

    def get_conditional_rules(self) -> list[dict[str, Any]]:
        return [
            {"field": target, "when": parse_condition(expression)}
            for target, expression in self.conditional_fields.items()
        ]

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        context = {
            "console_list_filter_submit": self.list_filter_submit,
            **(extra_context or {}),
        }
        return super().changelist_view(request, context)

    def changeform_view(
        self,
        request: HttpRequest,
        object_id: str | None = None,
        form_url: str = "",
        extra_context: dict[str, Any] | None = None,
    ) -> HttpResponse:
        context = {
            "console_warn_unsaved_form": self.warn_unsaved_form,
            "console_conditional_rules": self.get_conditional_rules(),
            **(extra_context or {}),
        }
        return super().changeform_view(request, object_id, form_url, context)

    def get_changelist_formset(self, request: HttpRequest, **kwargs: Any) -> Any:
        """``list_editable`` formset whose inputs never point at absent help text.

        Table cells render no help-text element, so a field that keeps its
        ``help_text`` would carry an ``aria-describedby`` that resolves to nothing.
        """
        formset = super().get_changelist_formset(request, **kwargs)
        for form_field in formset.form.base_fields.values():
            form_field.help_text = ""
        return formset

    def get_formset_kwargs(
        self, request: HttpRequest, obj: Model, inline: InlineModelAdmin, prefix: str
    ) -> dict[str, Any]:
        kwargs = super().get_formset_kwargs(request, obj, inline, prefix)
        per_page = getattr(inline, "per_page", None)
        if per_page:
            kwargs["request"] = request
            kwargs["per_page"] = per_page
        return kwargs

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        errors = list(super().check(**kwargs))
        try:
            self.get_conditional_rules()
        except ValueError as exc:
            errors.append(
                checks.Error(str(exc), obj=type(self), id="console.E003"),
            )
        return errors


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
    """django-solo singleton rendered with the console's own change form.

    solo's stock templates (``admin/solo/...``) replace the console's header tools
    with legacy markup, so the console's default templates are used instead.
    """

    change_form_template = "admin/change_form.html"
    object_history_template = "admin/object_history.html"

    def render_change_form(
        self, request: HttpRequest, context: dict[str, Any], *args: Any, **kwargs: Any
    ) -> HttpResponse:
        """Settings pages show their boolean options as switches (same inputs and names)."""
        context["console_switches"] = True
        return super().render_change_form(request, context, *args, **kwargs)


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


def active_request() -> HttpRequest | None:
    """The request whose change form is being rendered (``None`` outside of it)."""
    return _current_request.get()


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
        keep_request = False
        try:
            response = super().changeform_view(
                request, object_id, form_url, extra_context
            )
            if (
                isinstance(response, SimpleTemplateResponse)
                and not response.is_rendered
            ):
                response.add_post_render_callback(_forget_request)
                keep_request = True
        finally:
            if not keep_request:
                _current_request.set(None)
        return response

    @display(description=_("Related records"))
    def related_overview(self, obj: Model | None) -> SafeString | str:
        request = active_request()
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
            '<ul class="console-related-list">{}</ul>',
            format_html_join(
                "",
                '<li><a class="console-related-list__link" href="{}">{}</a> '
                '<span class="console-related-list__count">{}</span></li>',
                rows,
            ),
        )


class PaginatedInlineFormSet(BaseInlineFormSet):
    """Inline formset that shows ``per_page`` rows at a time.

    ``ModelAdmin.get_formset_kwargs`` passes ``request`` and ``per_page``; the page
    number comes from ``<prefix>-page`` (GET or POST), falling back to page 1.
    Templates read ``formset.paginator`` and ``formset.page`` when ``per_page`` is set.
    """

    paginator: Paginator
    page: Page

    def __init__(
        self,
        *args: Any,
        request: HttpRequest | None = None,
        per_page: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.request = request
        self.per_page = per_page
        super().__init__(*args, **kwargs)
        if per_page and self.queryset is not None:
            rows = self.queryset
            if not rows.ordered:
                rows = rows.order_by("pk")
            self.paginator = Paginator(rows, per_page)
            self.page = self.paginator.get_page(self.page_number())
            self._queryset = self.page.object_list

    @property
    def page_key(self) -> str:
        return f"{self.prefix}-page"

    def page_number(self) -> int:
        if self.request is None:
            return 1
        for source in (self.request.GET, self.request.POST):
            raw = source.get(self.page_key, "")
            if raw.isdecimal() and int(raw) > 0:
                return int(raw)
        return 1


class _InlineOptions:
    """Console defaults shared by both inline layouts."""

    extra = 0
    per_page: int | None = 25
    tab = False
    collapsible = False
    formset: type[BaseInlineFormSet] = PaginatedInlineFormSet


class TabularInline(ConsoleAutocompleteMixin, _InlineOptions, admin.TabularInline):
    pass


class StackedInline(ConsoleAutocompleteMixin, _InlineOptions, admin.StackedInline):
    pass


def image_preview(url: str | None, alt: str = "") -> SafeString | str:
    """Thumbnail for read-only preview fields (no inline style: admin CSP)."""
    if not url:
        return "—"
    return format_html(
        '<img src="{}" alt="{}" class="console-thumb" width="64" height="64">',
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
        widget=ConsoleTextareaWidget(attrs={"rows": 3}),
    )


def confirm_dialog(
    title: StrOrPromise,
    description: StrOrPromise = "",
    form_class: type[BaseDialogForm] = ConfirmForm,
    submit: StrOrPromise = "Confirm",
) -> ActionDialog:
    """Dialog options for a state-changing row/detail/list action.

    Row and detail actions are rendered as links, so anything that writes must use a
    dialog: the link only opens the confirmation form (a GET never mutates) and the
    change happens on its CSRF-protected POST.
    """
    return {
        "title": title,
        "description": description,
        "form_class": form_class,
        "form_submit_text": submit,
    }


def htmx_redirect(url: str) -> HttpResponse:
    """Finish a dialog action: HTMX follows ``HX-Redirect`` after the POST.

    The ``@action`` wrapper turns this into a plain 302 for non-HTMX requests, so the
    no-JavaScript flow is POST-Redirect-GET as well.
    """
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
