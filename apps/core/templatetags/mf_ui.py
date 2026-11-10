"""Template helpers for the shared Mobofirm UI components (``templates/components``)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

from django import forms, template
from django.core.paginator import Page
from django.template import Node, NodeList
from django.template.base import FilterExpression, Parser, Token
from django.template.context import Context
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.safestring import SafeString

from apps.core.geo import calling_code_label, country_label
from apps.core.widgets import ComboboxSelect

register = template.Library()

_CHECK_WIDGETS = (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)
_SELECT_WIDGETS = (forms.Select, forms.SelectMultiple)


def _widget_class(widget: forms.Widget) -> str:
    if isinstance(widget, forms.HiddenInput):
        return ""
    if isinstance(widget, _CHECK_WIDGETS):
        return "form-check-input"
    if isinstance(widget, _SELECT_WIDGETS):
        return "form-select"
    return "form-control"


@register.filter
def mf_widget(field: forms.BoundField) -> SafeString:
    """Render a bound field with Bootstrap classes and accessible error wiring.

    ``aria-describedby`` references ``<id>_help`` and ``<id>_errors``, which the
    ``components/form_field.html`` partial renders for the same field.
    """
    widget = field.field.widget
    existing = widget.attrs.get("class", "")
    classes = existing.split()
    css_class = _widget_class(widget)
    if css_class and css_class not in classes:
        classes.append(css_class)
    attrs: dict[str, str | bool] = {}
    describedby = []
    if field.help_text:
        describedby.append(f"{field.auto_id}_help")
    describedby.append(f"{field.auto_id}_errors")
    if field.errors:
        attrs["aria-invalid"] = "true"
        classes.append("is-invalid")
    attrs["aria-describedby"] = " ".join(describedby)
    if classes:
        attrs["class"] = " ".join(classes)
    if isinstance(widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)):
        return render_to_string(
            "components/choice_group.html",
            {"field": field, "describedby": attrs.get("aria-describedby", "")},
        )
    return field.as_widget(attrs=attrs)


@register.filter
def mf_is_group(field: forms.BoundField) -> bool:
    """Whether the field renders as a checkbox or radio group needing a group label."""
    return isinstance(
        field.field.widget, (forms.CheckboxSelectMultiple, forms.RadioSelect)
    )


@register.filter
def mf_is_check(field: forms.BoundField) -> bool:
    """Whether the field is a single checkbox, which renders label-after-input."""
    return isinstance(field.field.widget, forms.CheckboxInput)


@register.filter
def mf_is_combobox(field: forms.BoundField) -> bool:
    """Whether the field renders as the shared type-ahead combobox."""
    return isinstance(field.field.widget, ComboboxSelect)


@register.inclusion_tag("components/combobox.html")
def combobox_field(field: forms.BoundField) -> dict[str, object]:
    """Render a ``ComboboxSelect`` field with ``components/combobox.html``."""
    widget = field.field.widget
    if not isinstance(widget, ComboboxSelect):
        raise TypeError(f"{field.name} does not use a ComboboxSelect widget")
    raw = field.value()
    value = str(raw) if raw not in (None, "") else ""
    if widget.kinds == "calling_code":
        related = (
            field.form["country"].value() if "country" in field.form.fields else ""
        )
        display = calling_code_label(value, str(related or ""))
    else:
        display = country_label(value)
    describedby = []
    if field.help_text:
        describedby.append(f"{field.auto_id}_help")
    describedby.append(f"{field.auto_id}_errors")
    return {
        "input_id": field.auto_id,
        "name": field.html_name,
        "mode": "pick",
        "name_label": str(field.label),
        "kinds": widget.kinds,
        "value": value,
        "display": display or value,
        "placeholder": widget.placeholder,
        "required": field.field.required,
        "invalid": bool(field.errors),
        "describedby": " ".join(describedby),
        "detected": "true" if widget.detected and value else "",
    }


@register.filter
def elided_pages(page_obj: Page) -> Iterator[int | str]:
    """Page numbers around the current page, with the paginator's ellipsis marker."""
    return page_obj.paginator.get_elided_page_range(
        page_obj.number, on_each_side=1, on_ends=1
    )


class TableScrollNode(Node):
    """Wrap a wide table in a keyboard-focusable, labelled scroll region."""

    def __init__(self, label: FilterExpression, nodelist: NodeList) -> None:
        self.label = label
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        return format_html(
            '<div class="table-scroll" role="region" tabindex="0" '
            'aria-label="{}">{}</div>',
            self.label.resolve(context),
            self.nodelist.render(context),
        )


@register.tag("table_scroll")
def do_table_scroll(parser: Parser, token: Token) -> TableScrollNode:
    """``{% table_scroll "Firmware files" %}<table>...</table>{% endtable_scroll %}``."""
    bits = token.split_contents()
    if len(bits) != 2:
        raise template.TemplateSyntaxError(
            "table_scroll takes exactly one argument: the region label."
        )
    nodelist = parser.parse(("endtable_scroll",))
    parser.delete_first_token()
    return TableScrollNode(parser.compile_filter(bits[1]), nodelist)


_ALLAUTH_CRUMBS: Final[dict[str, str]] = {
    "account_email": "Email addresses",
    "account_email_verification_sent": "Verify email",
    "account_confirm_email": "Confirm email",
    "account_logout": "Sign out",
    "account_reauthenticate": "Confirm password",
    "account_change_password": "Change password",
    "account_set_password": "Set password",
    "account_reset_password": "Reset password",
    "account_reset_password_done": "Reset password",
    "account_reset_password_from_key": "Reset password",
    "account_reset_password_from_key_done": "Reset password",
    "socialaccount_connections": "Connected accounts",
    "socialaccount_signup": "Complete sign-up",
    "usersessions_list": "Active sessions",
}
_ALLAUTH_PREFIXES: Final[tuple[str, ...]] = (
    "socialaccount_",
    "usersessions_",
    "account_",
    "mfa_",
)


def allauth_crumb_label(url_name: str) -> str:
    """Breadcrumb label for an allauth page, from its URL name."""
    if url_name in _ALLAUTH_CRUMBS:
        return _ALLAUTH_CRUMBS[url_name]
    if url_name.startswith("mfa_"):
        return "Two-factor authentication"
    for prefix in _ALLAUTH_PREFIXES:
        if url_name.startswith(prefix):
            url_name = url_name.removeprefix(prefix)
            break
    return url_name.replace("_", " ").strip().capitalize() or "Account"


@register.simple_tag(takes_context=True)
def allauth_breadcrumbs(context: Context) -> SafeString:
    """Breadcrumbs for django-allauth pages rendered by ``allauth/layouts/base.html``."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    url_name = getattr(match, "url_name", "") or ""
    values: dict[str, str] = {"current": allauth_crumb_label(url_name)}
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        try:
            values["parent_url"] = reverse("users:dashboard")
        except NoReverseMatch:
            values["parent_url"] = ""
        if values["parent_url"]:
            values["parent_label"] = "Account"
    return SafeString(render_to_string("components/breadcrumbs.html", values))
