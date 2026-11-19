"""Public short-link redirect."""

from __future__ import annotations

import json
from hmac import compare_digest

from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseRedirect,
)
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_safe

from apps.core.app_service import app_enabled
from apps.core.internal_request import is_internal_crawl
from apps.distribution import services, shortlinks, whatsapp
from apps.distribution.constants import KIND_WHATSAPP
from apps.distribution.models import Channel, ShortLink


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request: HttpRequest, channel_id: int) -> HttpResponse:
    """Meta's WhatsApp webhook for one channel: handshake (GET) and events (POST).

    Machine endpoint, not a browser/session write: it carries no cookie authority and is
    exempt from CSRF only because Meta cannot send a token. Every POST must carry a valid
    ``X-Hub-Signature-256`` for the channel's app secret; without a stored app secret
    (and verify token for the handshake) the endpoint answers 404, so it is closed by
    default. Applying events is idempotent.
    """
    if not app_enabled("distribution"):
        raise Http404
    channel = Channel.objects.filter(pk=channel_id, kind=KIND_WHATSAPP).first()
    if channel is None:
        raise Http404
    values = services.stored_values(channel)
    if request.method == "GET":
        token = values.get("verify_token", "")
        sent = request.GET.get("hub.verify_token", "")
        if (
            token
            and request.GET.get("hub.mode") == "subscribe"
            and compare_digest(token.encode(), sent.encode())
        ):
            return HttpResponse(
                request.GET.get("hub.challenge", ""), content_type="text/plain"
            )
        return HttpResponseForbidden()
    secret = values.get("app_secret", "")
    if not secret:
        raise Http404
    body = request.body
    if len(body) > whatsapp.MAX_WEBHOOK_BYTES or not whatsapp.verify_signature(
        secret, body, request.headers.get("X-Hub-Signature-256", "")
    ):
        return HttpResponseForbidden()
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return HttpResponse(status=400)
    if isinstance(payload, dict):
        whatsapp.handle_payload(channel, payload)
    return HttpResponse("ok", content_type="text/plain")


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
    if not is_internal_crawl(request):
        shortlinks.record_click(link, bot=shortlinks.is_bot(request))
    response = HttpResponseRedirect(target)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer-when-downgrade"
    return response
