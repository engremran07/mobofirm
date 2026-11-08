"""Public short-link redirect."""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.views.decorators.http import require_safe

from apps.core.app_service import app_enabled
from apps.distribution import shortlinks
from apps.distribution.models import ShortLink


@require_safe
def go(request: HttpRequest, code: str) -> HttpResponse:
    if not app_enabled("distribution"):
        raise Http404
    link = ShortLink.objects.filter(code=code).first()
    if link is None:
        raise Http404
    target = shortlinks.safe_target(link.target_url)
    if not target:
        raise Http404
    shortlinks.record_click(link, bot=shortlinks.is_bot(request))
    response = HttpResponseRedirect(target)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer-when-downgrade"
    return response
