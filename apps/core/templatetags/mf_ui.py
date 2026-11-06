"""Template helpers for the shared Mobofirm UI components (``templates/components``)."""

from __future__ import annotations

from collections.abc import Iterator

from django import forms, template
from django.core.paginator import Page
from django.template import Node, NodeList
from django.template.base import FilterExpression, Parser, Token
from django.template.context import Context
from django.template.loader import render_to_string
from django.utils.html import format_html
from django.utils.safestring import SafeString

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
    if field.errors:
        attrs["aria-invalid"] = "true"
        classes.append("is-invalid")
        describedby.append(f"{field.auto_id}_errors")
    if describedby:
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
