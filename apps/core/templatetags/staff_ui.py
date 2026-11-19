"""Template tags for the custom staff pages and dialogs of the staff console.

Staff pages (``templates/admin/staff_page.html`` and the pages built on it) compose
only these tags and includes from ``templates/console/staff``. The markup, including
every Bootstrap class, lives in those component templates so a Bootstrap upgrade or a
re-skin touches the components, never a page. Wrapping components are block tags::

    {% load staff_ui %}
    {% staff_section title="Channels" %}
      {% staff_table label="Channel health" %}<thead>...</thead><tbody>...</tbody>{% endstaff_table %}
    {% endstaff_section %}
"""

from __future__ import annotations

from typing import Any

from django import template
from django.template.context import Context
from django.template.loader import render_to_string
from django.utils.safestring import SafeString
from django.utils.text import slugify

register = template.Library()

_STAFF = "console/staff/"
_HEAT_LEVELS = (
    "",
    "bg-primary bg-opacity-10",
    "bg-primary bg-opacity-25",
    "bg-primary bg-opacity-50 text-body",
    "bg-primary text-white",
)
_BUTTON_TONES = {
    "primary": "btn-primary",
    "success": "btn-success",
    "danger": "btn-outline-danger",
    "danger-solid": "btn-danger",
}


def _render(template_name: str, /, **context: Any) -> SafeString:
    return SafeString(render_to_string(f"{_STAFF}{template_name}.html", context))


def _anchor(identifier: str, title: str) -> str:
    return identifier or slugify(title) or "section"


@register.simple_block_tag(name="staff_card")
def staff_card(
    content: str,
    title: str = "",
    subtitle: str = "",
    ident: str = "",
    tone: str = "",
    cell: bool = False,
) -> SafeString:
    """A surface card with an optional heading; ``tone`` is ``warning``/``danger``.

    ``cell=True`` wraps the card as one column of a ``staff_grid``.
    """
    return _render(
        "card",
        content=content,
        title=title,
        subtitle=subtitle,
        ident=_anchor(ident, title) if title else "",
        tone=tone,
        cell=cell,
    )


@register.simple_block_tag(name="staff_section")
def staff_section(
    content: str,
    title: str,
    ident: str = "",
    intro: str = "",
    action_url: str = "",
    action_label: str = "",
) -> SafeString:
    """A labelled page section: an ``h2`` plus an optional intro and header action."""
    return _render(
        "section",
        content=content,
        title=title,
        ident=_anchor(ident, title),
        intro=intro,
        action_url=action_url,
        action_label=action_label,
    )


@register.simple_block_tag(name="staff_table")
def staff_table(content: str, label: str, caption: str = "") -> SafeString:
    """A table that scrolls inside its own focusable region (never the page)."""
    return _render("table", content=content, label=label, caption=caption)


@register.simple_block_tag(name="staff_notice")
def staff_notice(content: str, tone: str = "info") -> SafeString:
    """An inline status note: ``info`` (default), ``warning``, ``danger``, ``success``."""
    return _render("notice", content=content, tone=tone)


@register.simple_block_tag(name="staff_empty")
def staff_empty(content: str, title: str = "") -> SafeString:
    """A friendly empty state; the body may carry the next-step link."""
    return _render("empty", content=content, title=title)


@register.simple_block_tag(name="staff_row")
def staff_row(content: str, justify: str = "") -> SafeString:
    """Inline cluster of buttons/badges/forms that wraps on narrow screens."""
    return _render("row", content=content, justify=justify)


@register.simple_block_tag(name="staff_stack")
def staff_stack(content: str) -> SafeString:
    """Vertical rhythm for sibling blocks."""
    return _render("stack", content=content)


@register.simple_block_tag(name="staff_list")
def staff_list(content: str) -> SafeString:
    """A plain list of ``{% staff_item %}`` entries."""
    return _render("list", content=content)


@register.simple_block_tag(name="staff_item")
def staff_item(content: str, split: bool = False) -> SafeString:
    """One entry of ``staff_list``; ``split=True`` pushes a second ``<span>`` to the end."""
    return _render("item", content=content, split=split)


@register.simple_block_tag(name="staff_grid")
def staff_grid(content: str, wide: bool = False) -> SafeString:
    """Responsive card grid: stat tiles by default, ``wide=True`` for larger cards."""
    return _render("grid", content=content, wide=wide)


@register.simple_block_tag(name="staff_tabs")
def staff_tabs(content: str, label: str) -> SafeString:
    """A navigation list (page sections, ranges); fill it with ``{% staff_tab %}``."""
    return _render("tabs", content=content, label=label)


@register.simple_tag(name="staff_tab")
def staff_tab(
    url: str,
    label: str,
    current: bool = False,
    append: Any = "",
    active: Any = None,
    key: Any = None,
    selected: Any = None,
    hx_target: str = "",
) -> SafeString:
    """One link of ``staff_tabs``; the current one carries ``aria-current``.

    ``append`` is added to ``url`` (for example ``url="?days=" append=7``); when
    ``active`` equals ``append`` the link is the current one. ``key``/``selected``
    mark the link current when the tab's ``key`` equals the page's ``selected`` key.
    ``hx_target`` (a selector) also loads the tab into that region over HTMX.
    """
    is_current = (
        bool(current)
        or (active is not None and str(active) == str(append))
        or (key is not None and selected is not None and str(key) == str(selected))
    )
    return _render(
        "tab",
        url=f"{url}{append}",
        label=label,
        current=is_current,
        hx_target=hx_target,
    )


@register.simple_block_tag(name="staff_dl")
def staff_dl(content: str) -> SafeString:
    """A description list of label/value pairs; fill it with ``{% staff_dd %}``."""
    return _render("dl", content=content)


@register.simple_block_tag(name="staff_dd")
def staff_dd(content: str, label: str) -> SafeString:
    """One label/value pair of ``staff_dl``."""
    return _render("dd", content=content, label=label)


@register.simple_block_tag(name="staff_select")
def staff_select(
    content: str,
    ident: str,
    name: str,
    label: str,
    hide_label: bool = False,
) -> SafeString:
    """A labelled ``<select>``; the body is the ``<option>`` list."""
    return _render(
        "select",
        content=content,
        ident=ident,
        name=name,
        label=label,
        hide_label=hide_label,
    )


@register.simple_tag(name="staff_input")
def staff_input(
    ident: str,
    name: str,
    label: str,
    value: Any = "",
    input_type: str = "text",
    placeholder: str = "",
    maxlength: int = 0,
    required: bool = False,
    hide_label: bool = False,
    help_text: str = "",
    list_id: str = "",
) -> SafeString:
    """A labelled single-line input; ``list_id`` points at a ``<datalist>``."""
    return _render(
        "input",
        ident=ident,
        name=name,
        label=label,
        value=value if value is not None else "",
        input_type=input_type,
        placeholder=placeholder,
        maxlength=maxlength,
        required=required,
        hide_label=hide_label,
        help_text=help_text,
        list_id=list_id,
    )


@register.simple_tag(name="staff_textarea")
def staff_textarea(
    ident: str,
    name: str,
    label: str,
    value: Any = "",
    rows: int = 4,
    help_text: str = "",
) -> SafeString:
    """A labelled multi-line input."""
    return _render(
        "textarea",
        ident=ident,
        name=name,
        label=label,
        value=value if value is not None else "",
        rows=rows,
        help_text=help_text,
    )


@register.simple_tag(name="staff_check")
def staff_check(
    ident: str,
    name: str,
    label: str,
    checked: bool = False,
    value: str = "1",
    unchecked_value: str = "",
    hide_label: bool = False,
    form: str = "",
) -> SafeString:
    """A labelled checkbox; ``unchecked_value`` adds a hidden fallback for the form.

    ``hide_label`` keeps the label for assistive technology only (table row selectors);
    ``form`` is the id of the ``<form>`` it belongs to when it sits outside of it.
    """
    return _render(
        "check",
        ident=ident,
        name=name,
        label=label,
        checked=bool(checked),
        value=value,
        unchecked_value=unchecked_value,
        hide_label=hide_label,
        form=form,
    )


@register.simple_tag(name="staff_button")
def staff_button(
    label: str,
    href: str = "",
    dialog_url: str = "",
    include: str = "",
    tone: str = "",
    size: str = "",
    name: str = "",
    value: str = "",
    form: str = "",
    target: str = "",
    aria_label: str = "",
    hx_target: str = "",
    hx_push_url: bool = False,
) -> SafeString:
    """A button or link.

    ``href`` renders a link; with ``hx_target`` (a selector) the link also loads its
    response into that region over HTMX (``hx_push_url`` keeps the address bar in
    step) and still works as a normal link without JavaScript. ``dialog_url`` renders
    a link that opens the dialog in
    ``#console-dialog`` over HTMX and still works as a full page without JavaScript;
    with ``include`` (a form selector) it is a submit button that sends that form's
    values to the dialog. Without either it is a plain submit button
    (``name``/``value``/``form`` as usual).
    ``tone``: ``primary``, ``success``, ``danger`` (outline), ``danger-solid``.
    ``size``: ``sm`` or default.
    """
    if dialog_url:
        kind = "dialog-button" if include else "dialog-link"
    elif href:
        kind = "link"
    else:
        kind = "button"
    css = " ".join(
        part
        for part in (
            "btn",
            _BUTTON_TONES.get(tone, "btn-outline-secondary"),
            "btn-sm" if size == "sm" else "",
        )
        if part
    )
    return _render(
        "button",
        kind=kind,
        css=css,
        label=label,
        href=href,
        dialog_url=dialog_url,
        include=include,
        name=name,
        value=value,
        form=form,
        target=target,
        aria_label=aria_label,
        hx_target=hx_target,
        hx_push_url=hx_push_url,
    )


@register.simple_tag(name="staff_chip")
def staff_chip(label: str, count: Any = "", href: str = "") -> SafeString:
    """A compact pill with an optional count; a link when ``href`` is given."""
    return _render("chip", label=label, count=count, href=href)


@register.simple_block_tag(name="staff_quote")
def staff_quote(content: str, source: str = "") -> SafeString:
    """Quoted user content (for example the comment being moderated) with its source."""
    return _render("quote", content=content, source=source)


@register.simple_tag(name="staff_badge")
def staff_badge(text: Any, tone: str = "neutral") -> SafeString:
    """A status badge: ``success``, ``warning``, ``danger``, ``info`` or ``neutral``."""
    return _render("badge", text=text, tone=tone)


@register.simple_tag(name="staff_stat")
def staff_stat(
    label: str,
    value: Any,
    hint: str = "",
    hint_url: str = "",
    hint_label: str = "",
    unit: str = "",
) -> SafeString:
    """A stat tile: label, big value and an optional hint with an optional link."""
    return _render(
        "stat",
        label=label,
        value=value,
        hint=hint,
        hint_url=hint_url,
        hint_label=hint_label,
        unit=unit,
    )


@register.simple_tag(name="staff_meter")
def staff_meter(
    ident: str,
    label: str,
    value: Any,
    caption: str = "",
    low: int = 70,
    high: int = 90,
) -> SafeString:
    """A labelled gauge (0 to 100 percent), for example disk usage."""
    return _render(
        "meter",
        ident=ident,
        label=label,
        value=value,
        caption=caption,
        low=low,
        high=high,
    )


@register.simple_tag(takes_context=True, name="staff_pager")
def staff_pager(
    context: Context, page: Any, label: str = "Pages", hx_target: str = ""
) -> SafeString:
    """Previous/next links for a Django ``Page`` that keep the other query parameters.

    ``hx_target`` (a selector) also loads the other page into that region over HTMX.
    """
    request = context.get("request")
    base_query = ""
    if request is not None:
        params = request.GET.copy()
        params.pop("page", None)
        base_query = params.urlencode()
    return _render(
        "pager",
        page=page,
        label=label,
        base_query=base_query,
        hx_target=hx_target,
    )


@register.filter(name="staff_heat")
def staff_heat(level: Any) -> str:
    """Utility classes for a heat-map cell of intensity ``level`` (0 to 4)."""
    try:
        index = int(level)
    except (TypeError, ValueError):
        return ""
    return _HEAT_LEVELS[max(0, min(index, len(_HEAT_LEVELS) - 1))]
