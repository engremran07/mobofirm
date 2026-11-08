"""Template helpers for the shared "Request it" call to action."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from django import template
from django.urls import reverse

register = template.Library()

QUERY_LIMIT = 100
# kind -> (request page URL name, noun shown to the user, form field the query fills)
KINDS: dict[str, tuple[str, str, str]] = {
    "brand": ("firmwares:request_brand", "brand", "name"),
    "model": ("firmwares:request_model", "model", "name"),
    "variant": ("firmwares:request_variant", "variant", "name"),
    "firmware": ("firmwares:request_firmware", "firmware", "description"),
    "chipset": ("firmwares:request_chipset", "chipset", "name"),
}


def _pk(value: Any) -> int | None:
    """A primary key from a model instance, an int or a numeric string."""
    candidate = getattr(value, "pk", value)
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, str) and candidate.isdigit():
        return int(candidate)
    return None


def _clean(query: object) -> str:
    return " ".join(str(query or "").split())[:QUERY_LIMIT]


def request_url(
    kind: str, query: object = "", brand: Any = None, model: Any = None
) -> str:
    """Path of the request form for ``kind``, prefilled with what the user typed."""
    if kind not in KINDS:
        raise ValueError(f"Unknown request kind: {kind!r}")
    url_name, _noun, query_field = KINDS[kind]
    params: dict[str, object] = {}
    text = _clean(query)
    if text:
        params[query_field] = text
    if kind in {"model", "variant", "firmware"}:
        brand_id = _pk(brand) or _pk(getattr(model, "brand_id", None))
        if brand_id:
            params["brand"] = brand_id
    if kind in {"variant", "firmware"}:
        model_id = _pk(model)
        if model_id:
            params["model"] = model_id
    path = reverse(url_name)
    return f"{path}?{urlencode(params)}" if params else path


@register.simple_tag(takes_context=True)
def request_cta_target(
    context: template.Context,
    kind: str,
    query: object = "",
    brand: Any = None,
    model: Any = None,
) -> dict[str, object]:
    """Link, label and wording of the request CTA for the current visitor."""
    target = request_url(kind, query, brand, model)
    noun = KINDS[kind][1]
    user = getattr(context.get("request"), "user", None)
    signed_in = bool(getattr(user, "is_authenticated", False))
    if signed_in:
        url, label = target, f"Request a {noun}"
    else:
        url = f"{reverse('users:account_login')}?next={quote(target, safe='')}"
        label = f"Sign in to request a {noun}"
    return {
        "url": url,
        "label": label,
        "noun": noun,
        "signed_in": signed_in,
        "query": _clean(query),
    }
