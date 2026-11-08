from __future__ import annotations

from typing import Any

from django import template
from django.http import HttpRequest

from apps.consent.services import (
    active_policy,
    has_consent,
    needs_decision,
    read_state,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def consent_allows(context: template.Context, category: str) -> bool:
    """``{% consent_allows "analytics" as ok %}``; false when there is no request."""
    request = context.get("request")
    if not isinstance(request, HttpRequest):
        return False
    return has_consent(request, category)


@register.inclusion_tag("consent/banner.html", takes_context=True)
def consent_banner(context: template.Context) -> dict[str, Any]:
    """Server-rendered banner; empty once the visitor has decided."""
    request = context.get("request")
    if not isinstance(request, HttpRequest) or not needs_decision(request):
        return {"show": False}
    match = request.resolver_match
    if match is not None and match.view_name == "consent:privacy_centre":
        return {"show": False}
    return {
        "show": True,
        "policy": active_policy(),
        "state": read_state(request),
        "next": request.get_full_path(),
        # Published CMS legal pages (site chrome): the policy link is shown only
        # while its page is live, so the banner never links to a 404.
        "legal_links": context.get("legal_links", []),
    }
