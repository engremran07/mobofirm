"""Template helpers for the staff-console shell and its component library.

Components live in ``templates/console/components/``. Render them with the
isolated ``{% ui "badge" label="Live" %}`` / ``{% component "card" title="Queue" %}
...{% endcomponent %}`` tags (a component never sees the page's own ``title``, ``form``
and so on) or with ``{% include ... only %}``. Inside a ``component`` block use
``{% slot "actions" %}...{% endslot %}`` to fill the named regions of a component.

Asset tags (``console_assets``) and the admin-action helpers belong to ``console.py``
and ``console_admin.py``; nothing here touches the asset manifest.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any, Final

from django import template
from django.core.paginator import Page
from django.http import HttpRequest
from django.template import Context, Node, NodeList, TemplateSyntaxError
from django.template.base import FilterExpression, Parser, Token
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode
from django.utils.safestring import SafeString, mark_safe

from apps.core import admin_dashboard, admin_navigation, admin_shell

register = template.Library()

FULL_LAYOUT: Final = "console/layout.html"
PARTIAL_LAYOUT: Final = "console/base_partial.html"
COMPONENT_DIR: Final = "console/components/"
SLOTS_KEY: Final = "__console_slots__"
_COMPONENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(/[a-z][a-z0-9_]*)*$")
_ICON_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_ATTR_NAME = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]*$")
VARIANTS: Final = frozenset(
    {"primary", "success", "warning", "danger", "info", "neutral"}
)
_LEVEL_VARIANTS: Final = {
    "debug": "info",
    "info": "info",
    "success": "success",
    "warning": "warning",
    "error": "danger",
}
# What a component needs from the page without inheriting its other variables.
_PASSTHROUGH: Final = ("request", "csrf_token", "LANGUAGE_CODE", "LANGUAGE_BIDI")


# -- Layout selection ---------------------------------------------------------------
@register.filter
def console_layout(request: HttpRequest | str | None) -> str:
    """Layout of a console page (``admin/base.html`` extends ``console_layout``).

    HTMX requests (except boosted navigations and history restores, which swap the
    whole body) get the content-only ``base_partial.html``; every other request gets
    the full shell.
    """
    headers = getattr(request, "headers", None)
    if (
        headers is not None
        and headers.get("HX-Request") == "true"
        and headers.get("HX-Boosted") != "true"
        and headers.get("HX-History-Restore-Request") != "true"
    ):
        return PARTIAL_LAYOUT
    return FULL_LAYOUT


# -- Icons ----------------------------------------------------------------------------
@register.simple_tag
def console_icon(name: str, css_class: str = "", label: str = "") -> SafeString:
    """A Lucide icon placeholder, replaced by the SVG when the icon set loads.

    Decorative unless ``label`` is given, which makes it a labelled image.
    """
    if not _ICON_NAME.match(name):
        raise TemplateSyntaxError(f"Invalid icon name {name!r}.")
    classes = f"c-icon {css_class}".strip()
    if label:
        return format_html(
            '<i data-lucide="{}" class="{}" role="img" aria-label="{}"></i>',
            name,
            classes,
            label,
        )
    return format_html(
        '<i data-lucide="{}" class="{}" aria-hidden="true"></i>', name, classes
    )


# -- Components -----------------------------------------------------------------------
def _component_template(name: str) -> str:
    if not _COMPONENT_NAME.match(name):
        raise TemplateSyntaxError(f"Invalid component name {name!r}.")
    return f"{COMPONENT_DIR}{name}.html"


def _render_component(
    context: Context, name: str, values: Mapping[str, Any]
) -> SafeString:
    current = context.template
    if current is None:
        raise TemplateSyntaxError("Components can only render inside a template.")
    loaded = current.engine.get_template(_component_template(name))
    isolated: dict[str, Any] = {
        key: context[key] for key in _PASSTHROUGH if key in context
    }
    isolated.update(values)
    return mark_safe(loaded.render(context.new(isolated)))


def _parse_kwargs(
    parser: Parser, bits: list[str], tag_name: str
) -> dict[str, FilterExpression]:
    kwargs: dict[str, FilterExpression] = {}
    for bit in bits:
        key, separator, value = bit.partition("=")
        if not separator or not key.isidentifier():
            raise TemplateSyntaxError(
                f"{tag_name} arguments must be key=value pairs, got {bit!r}."
            )
        kwargs[key] = parser.compile_filter(value)
    return kwargs


class UiNode(Node):
    """Self-closing component: ``{% ui "badge" label="Live" variant="success" %}``."""

    def __init__(
        self, name: FilterExpression, kwargs: dict[str, FilterExpression]
    ) -> None:
        self.name = name
        self.kwargs = kwargs

    def render(self, context: Context) -> str:
        values = {key: expr.resolve(context) for key, expr in self.kwargs.items()}
        return _render_component(context, str(self.name.resolve(context)), values)


class SlotNode(Node):
    def __init__(self, name: str, nodelist: NodeList) -> None:
        self.name = name
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        slots = context.get(SLOTS_KEY)
        if slots is None:
            raise TemplateSyntaxError(
                "{% slot %} is only valid inside {% component %}."
            )
        slots[self.name] = self.nodelist.render(context)
        return ""


class ComponentNode(Node):
    """Block component: the body becomes ``content``, ``{% slot %}`` blocks ``slots``."""

    def __init__(
        self,
        name: FilterExpression,
        kwargs: dict[str, FilterExpression],
        nodelist: NodeList,
    ) -> None:
        self.name = name
        self.kwargs = kwargs
        self.nodelist = nodelist

    def render(self, context: Context) -> str:
        values = {key: expr.resolve(context) for key, expr in self.kwargs.items()}
        slots: dict[str, SafeString] = {}
        with context.push():
            context[SLOTS_KEY] = slots
            body = self.nodelist.render(context)
        filled = {key: mark_safe(value) for key, value in slots.items()}
        values["content"] = mark_safe(body.strip())
        values["slots"] = filled
        return _render_component(context, str(self.name.resolve(context)), values)


@register.tag("ui")
def do_ui(parser: Parser, token: Token) -> UiNode:
    bits = token.split_contents()
    if len(bits) < 2:
        raise TemplateSyntaxError("ui requires a component name.")
    return UiNode(parser.compile_filter(bits[1]), _parse_kwargs(parser, bits[2:], "ui"))


@register.tag("component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    bits = token.split_contents()
    if len(bits) < 2:
        raise TemplateSyntaxError("component requires a component name.")
    kwargs = _parse_kwargs(parser, bits[2:], "component")
    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()
    return ComponentNode(parser.compile_filter(bits[1]), kwargs, nodelist)


@register.tag("slot")
def do_slot(parser: Parser, token: Token) -> SlotNode:
    bits = token.split_contents()
    if len(bits) != 2 or bits[1][0] not in "\"'":
        raise TemplateSyntaxError('slot takes one quoted name: {% slot "actions" %}.')
    nodelist = parser.parse(("endslot",))
    parser.delete_first_token()
    return SlotNode(bits[1][1:-1], nodelist)


# -- Shell data -----------------------------------------------------------------------
@register.simple_tag(takes_context=True)
def console_shell(context: Context) -> dict[str, Any]:
    """Branding, sidebar and palette data; reuses what the view already put in context."""
    keys = (
        "console_brand",
        "sidebar_navigation",
        "console_palette_items",
        "console_account_links",
    )
    if all(key in context for key in keys):
        return {key: context[key] for key in keys}
    return admin_shell.shell_context(context["request"])


@register.simple_tag(takes_context=True)
def console_dashboard(context: Context) -> dict[str, Any]:
    return admin_dashboard.dashboard_page_context(context["request"])


@register.simple_tag(takes_context=True)
def console_breadcrumbs(context: Context) -> list[dict[str, str]]:
    """Default trail: Dashboard, app, model, then the current object or page."""
    crumbs = [{"label": str(context.get("home_label", "Dashboard")), "url": _index()}]
    opts = context.get("opts")
    title = str(context.get("title") or "")
    if opts is None:
        if title:
            crumbs.append({"label": title, "url": ""})
        return crumbs
    crumbs.append({"label": str(opts.app_config.verbose_name), "url": _app_url(opts)})
    model_url = _model_url(opts) if context.get("has_view_permission", True) else ""
    model_label = str(opts.verbose_name_plural).capitalize()
    original = context.get("original")
    if "cl" in context or (original is None and not context.get("add")):
        crumbs.append({"label": model_label, "url": ""})
    elif original is not None:
        crumbs.append({"label": model_label, "url": model_url})
        crumbs.append({"label": str(original), "url": ""})
    else:
        crumbs.append({"label": model_label, "url": model_url})
        crumbs.append({"label": title or "Add", "url": ""})
    return crumbs


def _index() -> str:
    return reverse("admin:index")


def _app_url(opts: Any) -> str:
    try:
        return reverse("admin:app_list", kwargs={"app_label": opts.app_label})
    except NoReverseMatch:
        return ""


def _model_url(opts: Any) -> str:
    try:
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    except NoReverseMatch:
        return ""


@register.simple_tag(takes_context=True)
def console_model_tabs(context: Context) -> list[dict[str, Any]]:
    """Status tabs for the current model's pages (empty when the model has none)."""
    opts = context.get("opts")
    request = context.get("request")
    if opts is None or request is None:
        return []
    return admin_navigation.tabs_for_path(request, opts.label_lower)


# -- Query strings and pagination -------------------------------------------------------
@register.simple_tag(takes_context=True)
def console_query(context: Context, **updates: Any) -> str:
    """Current query string with ``updates`` applied (``None``/'' removes a key)."""
    request = context["request"]
    params = request.GET.copy()
    for key, value in updates.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[key] = str(value)
    encoded = urlencode(params, doseq=True)
    return f"?{encoded}" if encoded else "?"


@register.simple_tag(takes_context=True)
def console_page_query(context: Context, number: int, param: str = "page") -> str:
    """Query string that shows page ``number`` (the ``param`` key) and keeps the rest."""
    return console_query(context, **{param or "page": number})


@register.filter
def console_pages(page_obj: Page) -> Iterator[dict[str, Any]]:
    """Page numbers around the current page; gaps are ``{"gap": True}``."""
    for number in page_obj.paginator.get_elided_page_range(
        page_obj.number, on_each_side=1, on_ends=1
    ):
        if number == page_obj.paginator.ELLIPSIS:
            yield {"gap": True}
        else:
            yield {"gap": False, "number": number, "current": number == page_obj.number}


# -- Small value helpers ------------------------------------------------------------------
@register.filter
def console_variant(value: object, default: str = "neutral") -> str:
    """Clamp a colour name to the supported variants."""
    name = str(value or "").lower()
    return name if name in VARIANTS else default


@register.filter
def console_level(message: Any) -> str:
    """Variant of a Django message (``error`` maps to ``danger``)."""
    tag = (str(getattr(message, "level_tag", "")).split() or [""])[0]
    return _LEVEL_VARIANTS.get(tag, "info")


@register.filter
def console_initials(user: Any) -> str:
    """Up to two initials for an avatar, from the full name or the username."""
    name = ""
    if hasattr(user, "get_full_name"):
        name = (user.get_full_name() or "").strip()
    name = name or str(getattr(user, "get_username", lambda: "")() or "?")
    parts = re.split(r"[\s._@-]+", name)
    letters = "".join(part[0] for part in parts if part)[:2]
    return letters.upper() or "?"


@register.filter
def console_get(value: Any, key: str) -> Any:
    """``value[key]`` for mappings, ``getattr`` otherwise; ``""`` when missing."""
    if isinstance(value, Mapping):
        return value.get(key, "")
    return getattr(value, key, "")


@register.filter
def console_attrs(attrs: Mapping[str, Any] | None) -> SafeString:
    """Render extra HTML attributes from a dict.

    ``True`` renders a bare attribute, ``None``/``False`` is skipped. Inline event
    handlers and ``style`` are refused: the console runs under a strict CSP.
    """
    if not attrs:
        return mark_safe("")
    pairs: list[tuple[str, Any]] = []
    for key, value in attrs.items():
        lowered = str(key).lower()
        if (
            not _ATTR_NAME.match(str(key))
            or lowered.startswith("on")
            or lowered == "style"
        ):
            raise TemplateSyntaxError(f"Attribute {key!r} is not allowed.")
        if value is None or value is False:
            continue
        pairs.append((str(key), value))
    return format_html_join(
        "",
        "{}",
        (
            (
                format_html(" {}", key)
                if value is True
                else format_html(' {}="{}"', key, value),
            )
            for key, value in pairs
        ),
    )
