"""Template helpers of the staff console CRUD pages (``templates/admin`` overrides).

Everything here only reshapes data that ``django.contrib.admin`` already computed so the
templates stay declarative: changelist rows with their keys and URLs, active-filter
chips, form layout (plain fieldsets vs. tabs), inline formset rows, widget styling and
the HTMX dialog detection. No helper bypasses a permission check: URLs and actions are
only produced for what the user may reach, the views remain the authority.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any

from django import forms, template
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.templatetags.admin_list import (
    ResultList,
    date_hierarchy,
    result_headers,
    result_hidden_fields,
    results,
)
from django.contrib.admin.templatetags.admin_urls import add_preserved_filters
from django.contrib.admin.utils import quote
from django.contrib.admin.views.main import PAGE_VAR, SEARCH_VAR, ChangeList
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.db.models.options import Options
from django.forms import Media
from django.template.loader import get_template
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html_join
from django.utils.safestring import SafeString
from django.utils.translation import gettext as _

from apps.core.admin_forms import merge_css_classes
from apps.core.admin_widgets import (
    ConsoleAutocompleteSelect,
    ConsoleAutocompleteSelectMultiple,
)

register = template.Library()

DIALOG_HOST_ID = "console-dialog"
CHECKBOX_COLUMN_CLASS = "action-checkbox-column"
RESULTS_TEMPLATE = "console/crud/results_table.html"
SWITCH_CLASS = "c-switch-input"

_BUTTON_CLASSES = {
    "default": "btn-outline-secondary",
    "primary": "btn-primary",
    "success": "btn-success",
    "warning": "btn-warning",
    "danger": "btn-danger",
    "info": "btn-info",
}

_AUTOCOMPLETE = (ConsoleAutocompleteSelect, ConsoleAutocompleteSelectMultiple)
_CHOICE_GROUP_TEMPLATES = (
    (forms.RadioSelect, "console/widgets/radio_select.html"),
    (forms.CheckboxSelectMultiple, "console/widgets/checkbox_select_multiple.html"),
)
_TEMPORAL = (
    (forms.DateTimeInput, "datetime-local", "%Y-%m-%dT%H:%M:%S"),
    (forms.DateInput, "date", "%Y-%m-%d"),
    (forms.TimeInput, "time", "%H:%M:%S"),
)
_NEEDS_VARIANT = (
    forms.RadioSelect,
    forms.CheckboxSelectMultiple,
    forms.DateTimeInput,
    forms.DateInput,
    forms.TimeInput,
    forms.MultiWidget,
)
_WIDE_WIDGETS = (
    forms.Textarea,
    forms.CheckboxSelectMultiple,
    forms.SelectMultiple,
    *_AUTOCOMPLETE,
)


@register.filter
def console_button_class(variant: object) -> str:
    """Bootstrap button class for an action variant (``ActionVariant`` or its value)."""
    key = variant.value if isinstance(variant, Enum) else variant
    return _BUTTON_CLASSES.get(str(key or "default"), _BUTTON_CLASSES["default"])


@register.filter
def console_action_variant(variant: object) -> str:
    """The plain string value of an ``ActionVariant`` (``default`` when unset)."""
    key = variant.value if isinstance(variant, Enum) else variant
    return str(key or "default")


@register.simple_tag(takes_context=True)
def console_dialog_request(context: template.Context) -> bool:
    """True when HTMX asked for the response to be loaded into the dialog host."""
    request = context.get("request")
    if request is None:
        return False
    target = request.headers.get("HX-Target", "")
    return target.rsplit("#", 1)[-1] == DIALOG_HOST_ID


@register.simple_tag(takes_context=True)
def console_parent_url(context: template.Context) -> str:
    """URL one path segment above the current page (where an action dialog came from)."""
    path = str(context["request"].path)
    return path.rstrip("/").rpartition("/")[0] + "/"


# -- Widgets and fields -----------------------------------------------------------------
def _unwrap(widget: forms.Widget) -> forms.Widget:
    while isinstance(widget, RelatedFieldWidgetWrapper):
        widget = widget.widget
    return widget


def widget_css_class(widget: forms.Widget) -> str:
    """Bootstrap class matching the kind of ``widget`` (empty when it styles itself)."""
    inner = _unwrap(widget)
    if inner.is_hidden or isinstance(inner, _AUTOCOMPLETE):
        return ""
    if isinstance(inner, forms.CheckboxInput):
        return "form-check-input"
    if isinstance(inner, forms.RadioSelect | forms.CheckboxSelectMultiple):
        return ""
    if isinstance(inner, forms.Select | forms.SelectMultiple):
        return "form-select"
    return "form-control"


def _style_widget(widget: forms.Widget) -> None:
    """Add the widget's Bootstrap class in place (list_editable cells use stock rendering)."""
    css = widget_css_class(widget)
    if css:
        inner = _unwrap(widget)
        inner.attrs["class"] = merge_css_classes(inner.attrs.get("class"), css)


def _as_switch(widget: forms.Widget) -> None:
    """Turn a boolean checkbox into a switch in place: same input, name and value, new look and role."""
    inner = _unwrap(widget)
    if isinstance(inner, forms.CheckboxInput):
        classes = str(inner.attrs.get("class", "")).replace("form-check-input", "")
        inner.attrs["class"] = merge_css_classes(classes.strip() or None, SWITCH_CLASS)
        inner.attrs["role"] = "switch"
        inner.attrs["data-ui-switch"] = True


def _variant[W: forms.Widget](widget: W, change: Callable[[forms.Widget], None]) -> W:
    """Copy of ``widget`` (and of the widget inside a related wrapper) with ``change`` applied.

    Widgets are shared between requests through the form class, so they are never
    modified in place at render time.
    """
    if isinstance(widget, RelatedFieldWidgetWrapper):
        wrapper = copy.copy(widget)
        wrapper.widget = _variant(widget.widget, change)
        return wrapper
    clone = copy.copy(widget)
    clone.attrs = dict(widget.attrs)
    change(clone)
    return clone


def _native_temporal(widget: forms.Widget) -> None:
    """Date/time inputs become the browser's pickers (ISO values, accepted by Django)."""
    for base, input_type, value_format in _TEMPORAL:
        if isinstance(widget, base):
            widget.input_type = input_type
            widget.format = value_format
            widget.attrs.pop("size", None)
    if isinstance(widget, forms.MultiWidget):
        widget.widgets = [_variant(sub, _native_temporal) for sub in widget.widgets]


def _console_widget(widget: forms.Widget) -> forms.Widget | None:
    """A console-rendered copy of ``widget`` when stock rendering is not enough."""

    def change(clone: forms.Widget) -> None:
        for group, template_name in _CHOICE_GROUP_TEMPLATES:
            if isinstance(clone, group) and not clone.template_name.startswith(
                "console/"
            ):
                clone.template_name = template_name
        _native_temporal(clone)

    inner = _unwrap(widget)
    if isinstance(inner, _NEEDS_VARIANT):
        return _variant(widget, change)
    return None


@register.simple_tag(takes_context=True)
def console_id_prefix(context: template.Context) -> str:
    """Prefix for element ids inside the dialog, so they never clash with the page's ids."""
    return "dlg-" if console_dialog_request(context) else ""


@register.simple_tag
def console_field_widget(
    admin_field: Any, id_prefix: str = "", switch: bool = False
) -> SafeString:
    """Render the input of an ``AdminField`` with the console's Bootstrap classes.

    ``aria-invalid`` and ``aria-describedby`` (``<auto_id>_helptext`` and
    ``<auto_id>_error``, the ids the field template gives the help and error nodes)
    come from Django's own ``BoundField``. With ``id_prefix`` (dialogs) the input id and
    its description ids are prefixed so they stay unique next to the page's own form.
    ``switch=True`` renders a boolean checkbox as a switch (``role=switch``); the input,
    its name and its submitted value are unchanged, so it still works without JavaScript.
    """
    bound = admin_field.field
    widget = bound.field.widget
    attrs: dict[str, Any] = {}
    css = widget_css_class(widget)
    is_switch = switch and isinstance(_unwrap(widget), forms.CheckboxInput)
    if is_switch:
        attrs["class"] = merge_css_classes(
            _unwrap(widget).attrs.get("class"), SWITCH_CLASS
        )
        attrs["role"] = "switch"
        attrs["data-ui-switch"] = True
    elif css:
        attrs["class"] = merge_css_classes(_unwrap(widget).attrs.get("class"), css)
    if id_prefix and bound.auto_id and not widget.is_hidden:
        attrs["id"] = f"{id_prefix}{bound.auto_id}"
        described = []
        if bound.help_text:
            described.append(f"{id_prefix}{bound.auto_id}_helptext")
        if bound.errors:
            described.append(f"{id_prefix}{bound.auto_id}_error")
        if described and not bound.use_fieldset:
            attrs["aria-describedby"] = " ".join(described)
    return bound.as_widget(widget=_console_widget(widget), attrs=attrs)


@register.filter
def console_field_is_wide(admin_field: Any) -> bool:
    """Single-field lines that need the full row (text areas, multi-choice widgets)."""
    if admin_field.is_readonly:
        return True
    return isinstance(_unwrap(admin_field.field.field.widget), _WIDE_WIDGETS)


# -- Changelist -------------------------------------------------------------------------
def _style_list_editable(cl: ChangeList) -> None:
    formset = getattr(cl, "formset", None)
    if formset is None:
        return
    for form in formset.forms:
        for name, field in form.fields.items():
            _style_widget(field.widget)
            _as_switch(field.widget)
            inner = _unwrap(field.widget)
            if "aria-label" not in inner.attrs:
                inner.attrs["aria-label"] = str(
                    field.label or name.replace("_", " ").title()
                )


def _row_urls(
    context: template.Context, cl: ChangeList, obj: Any, pk: str
) -> dict[str, Any]:
    request = context["request"]
    model_admin = cl.model_admin
    base: dict[str, Options[Any] | str] = {
        "preserved_filters": str(context.get("preserved_filters", "")),
        "opts": cl.opts,
    }
    urls: dict[str, Any] = {}
    for suffix in ("change", "history", "delete"):
        try:
            raw = reverse(
                f"admin:{cl.opts.app_label}_{cl.opts.model_name}_{suffix}", args=[pk]
            )
        except NoReverseMatch:
            continue
        urls[suffix] = add_preserved_filters(base, raw)
    urls["can_change"] = model_admin.has_change_permission(request, obj)
    urls["can_delete"] = model_admin.has_delete_permission(request, obj)
    return urls


@register.inclusion_tag(RESULTS_TEMPLATE, takes_context=True)
def console_result_list(context: template.Context, cl: ChangeList) -> dict[str, Any]:
    """Headers and rows of the changelist, each row carrying its object, key and URLs."""
    _style_list_editable(cl)
    headers = list(result_headers(cl))
    for header in headers:
        header["is_checkbox"] = CHECKBOX_COLUMN_CLASS in str(
            header.get("class_attrib", "")
        )
    has_checkbox = bool(headers) and headers[0]["is_checkbox"]
    rows = []
    for obj, row in zip(cl.result_list, results(cl), strict=False):
        pk = quote(obj.pk)
        rows.append(
            {
                "obj": obj,
                "pk": pk,
                "cells": list(row)[1:] if has_checkbox else list(row),
                "form": row.form if isinstance(row, ResultList) else None,
                "urls": {} if cl.is_popup else _row_urls(context, cl, obj, pk),
            }
        )
    has_row_actions = not cl.is_popup
    return {
        "cl": cl,
        "request": context.get("request"),
        "opts": cl.opts,
        "result_hidden_fields": list(result_hidden_fields(cl)),
        "result_headers": headers,
        "num_sorted_fields": sum(
            1 for h in headers if h["sortable"] and h.get("sorted")
        ),
        "rows": rows,
        "has_checkbox": has_checkbox,
        "action_checkbox_name": ACTION_CHECKBOX_NAME,
        "actions_row": context.get("actions_row") or [],
        "has_row_actions": has_row_actions,
        "column_count": len(headers) + (1 if has_row_actions else 0),
    }


@register.inclusion_tag("admin/date_hierarchy.html", takes_context=True)
def console_date_hierarchy(context: template.Context, cl: ChangeList) -> dict[str, Any]:
    """Django's date drill-down data plus the changelist and popup flag."""
    data = date_hierarchy(cl) or {"show": False}
    return {**data, "cl": cl, "is_popup": context.get("is_popup", False)}


@register.simple_tag
def console_page_url(cl: ChangeList, page: int) -> str:
    """Query string that shows ``page`` of the changelist."""
    return cl.get_query_string({PAGE_VAR: page})


@register.simple_tag
def console_carry_over(cl: ChangeList, *own: str) -> list[tuple[str, str]]:
    """Other active query parameters, for a GET form that must keep them."""
    return [(name, value) for name, value in cl.params.items() if name not in own]


@register.simple_tag(takes_context=True)
def console_filter(
    context: template.Context, cl: ChangeList, spec: Any, index: int
) -> SafeString:
    """Render one filter with the extra context the console filter templates use."""
    choices = list(spec.choices(cl))
    used = getattr(spec, "used_parameters", None) or {}
    request = context.get("request")
    return SafeString(
        get_template(spec.template).render(
            {
                "title": spec.title,
                "choices": choices,
                "spec": spec,
                "cl": cl,
                "index": index,
                "active": bool(used),
                "uid": f"c-filter-{index}",
                "path": request.path if request is not None else "",
            }
        )
    )


@register.simple_tag
def console_filter_chips(cl: ChangeList) -> list[dict[str, str]]:
    """Active search/filter summary: one removable chip per active control."""
    chips: list[dict[str, str]] = []
    if cl.query:
        chips.append(
            {
                "title": _("Search"),
                "label": cl.query,
                "remove_url": cl.get_query_string(remove=[SEARCH_VAR]),
            }
        )
    for spec in cl.filter_specs:
        used = getattr(spec, "used_parameters", None)
        if not used or not any(_flatten(used.values())):
            continue
        label = _range_label(spec) or _selected_label(cl, spec)
        if not label:
            label = ", ".join(value for value in _flatten(used.values()) if value != "")
        chips.append(
            {
                "title": str(spec.title),
                "label": label,
                "remove_url": cl.get_query_string(
                    remove=[name for name in spec.expected_parameters() or [] if name]
                ),
            }
        )
    return chips


def _flatten(values: Iterable[Any]) -> list[str]:
    flat: list[str] = []
    for value in values:
        if isinstance(value, list | tuple):
            flat.extend(str(item) for item in value)
        else:
            flat.append(str(value))
    return flat


def _range_label(spec: Any) -> str:
    low = getattr(spec, "value_from", "")
    high = getattr(spec, "value_to", "")
    if low and high:
        return f"{low} – {high}"
    if low:
        return f"≥ {low}"
    return f"≤ {high}" if high else ""


def _selected_label(cl: ChangeList, spec: Any) -> str:
    labels = [
        str(choice["display"])
        for choice in spec.choices(cl)
        if choice.get("selected") and choice.get("display") is not None
    ]
    return ", ".join(label for label in labels if label)


# -- Change form --------------------------------------------------------------------------
def _is_tab(classes: str) -> bool:
    return "tab" in classes.split()


def _fieldset_errors(fieldset: Any) -> int:
    count = 0
    for line in fieldset:
        for field in line:
            if not field.is_readonly:
                count += len(field.field.errors)
    return count


def _inline_errors(inline_admin_formset: Any) -> int:
    return int(inline_admin_formset.formset.total_error_count())


@register.simple_tag
def console_fieldset_errors(fieldset: Any) -> int:
    """Number of validation errors in the fieldset's editable fields."""
    return _fieldset_errors(fieldset)


@register.simple_tag
def console_form_layout(
    adminform: Any, inline_admin_formsets: Iterable[Any], id_prefix: str = ""
) -> dict[str, Any]:
    """Split a change form into always-visible blocks and tab panes.

    Fieldsets whose ``classes`` contain ``tab`` and inlines declared with
    ``tab = True`` become tab panes (the tab title is the fieldset name or the inline's
    plural name); everything else keeps the stock order: fieldsets, then inlines.
    """
    main: list[dict[str, Any]] = []
    tabs: list[dict[str, Any]] = []
    plain_inlines: list[Any] = []
    for index, fieldset in enumerate(adminform):
        entry = {"fieldset": fieldset, "index": index}
        if _is_tab(str(fieldset.classes)):
            tabs.append(
                {
                    **entry,
                    "kind": "fieldset",
                    "title": fieldset.name or _("Details"),
                    "errors": _fieldset_errors(fieldset),
                }
            )
        else:
            main.append(entry)
    for index, inline in enumerate(inline_admin_formsets):
        if getattr(inline.opts, "tab", False):
            title = (
                inline.opts.verbose_name
                if inline.formset.max_num == 1
                else inline.opts.verbose_name_plural
            )
            tabs.append(
                {
                    "kind": "inline",
                    "inline": inline,
                    "index": index,
                    "title": str(title).capitalize(),
                    "errors": _inline_errors(inline),
                }
            )
        else:
            plain_inlines.append(inline)
    for number, tab in enumerate(tabs):
        tab["id"] = f"{id_prefix}c-tab-{number}"
    return {
        "main": main,
        "tabs": tabs,
        "plain_inlines": plain_inlines,
        "total_errors": sum(tab["errors"] for tab in tabs),
    }


@register.simple_tag
def console_inline_rows(inline_admin_formset: Any) -> dict[str, Any]:
    """Rows of an inline formset plus the blank form the "add" control clones.

    Django yields the blank form last when the user may add rows; the console renders it
    inside a ``<template>`` (the formset script replaces ``__prefix__``) instead of as a
    hidden row.
    """
    rows = list(inline_admin_formset)
    blank = rows.pop() if inline_admin_formset.has_add_permission else None
    formset = inline_admin_formset.formset
    paginator = getattr(formset, "paginator", None)
    return {
        "rows": rows,
        "blank": blank,
        "can_delete": bool(
            formset.can_delete and inline_admin_formset.has_delete_permission
        ),
        "is_single": formset.max_num == 1,
        "paginated": paginator is not None and paginator.num_pages > 1,
    }


@register.simple_tag(takes_context=True)
def console_inline_page_url(
    context: template.Context, formset: Any, number: int
) -> str:
    """Query string showing page ``number`` of one inline formset (other params kept)."""
    params = context["request"].GET.copy()
    params[formset.page_key] = str(number)
    return f"?{params.urlencode()}#{formset.prefix}-group"


@register.simple_tag
def console_media(media: Media) -> SafeString:
    """Form media minus Django's bundled admin scripts and styles.

    The console replaces ``admin/js`` behaviour (calendar, related popups, select2,
    inlines, actions) with its own HTMX/Alpine components, so only assets shipped by
    the project's own widgets (for example the rich-text editor) are emitted.
    """
    tags = [
        tag
        for tag in (*media.render_css(), *media.render_js())
        if "/admin/js/" not in tag and "/admin/css/" not in tag
    ]
    return format_html_join("\n", "{}", ((tag,) for tag in tags))
