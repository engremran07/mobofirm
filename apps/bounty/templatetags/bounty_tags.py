from __future__ import annotations

from typing import Any

from django import template
from django.urls import reverse

from apps.bounty import services, targets

register = template.Library()


@register.inclusion_tag("bounty/_cta.html", takes_context=True)
def bounty_cta(context: template.Context, kind: str, target: Any) -> dict[str, Any]:
    """Optional "add a bounty" step for a firmware request, model or variant.

    ``{% load bounty_tags %}{% bounty_cta "request" firmware_request %}`` renders the
    open credits on that target and a link to ``/bounty/new/``. Nothing is rendered
    when bounties are paused or ``target`` is empty.
    """
    ok, _reason = services.availability()
    pk = getattr(target, "pk", target)
    if not ok or kind not in targets.TARGET_MODELS or not isinstance(pk, int):
        return {"show": False}
    count, open_total = services.open_credits_for(kind, pk)
    request = context.get("request")
    return {
        "show": True,
        "count": count,
        "credits": open_total,
        "new_url": f"{reverse('bounty:create')}?{kind}={pk}",
        "signed_in": bool(request and request.user.is_authenticated),
    }
