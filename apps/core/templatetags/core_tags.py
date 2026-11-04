"""Shared template tags for the platform — pagination, query strings, etc."""

from __future__ import annotations

from django import template
from django.http import HttpRequest

register = template.Library()


@register.simple_tag(takes_context=True)
def query_string(context: dict, **kwargs: object) -> str:  # type: ignore[type-arg]
    """Build a query string preserving current GET params, overriding with *kwargs*.

    Usage::

        {% load core_tags %}
        <a href="{% query_string page=3 %}">Page 3</a>
        <a href="{% query_string page=page_obj.next_page_number %}">Next</a>
    """
    request: HttpRequest | None = context.get("request")
    if not request:
        return ""
    params = request.GET.copy()
    for key, value in kwargs.items():
        if value is None or value == "":
            params.pop(key, None)
        else:
            params[str(key)] = str(value)
    qs = params.urlencode()
    return f"?{qs}" if qs else "?"
