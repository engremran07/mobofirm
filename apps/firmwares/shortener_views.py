"""First-party short download link: ``/d/<code>/``.

The code is only a key. The destination is never read from the request or stored: it is
the download page (gate) of the approved firmware the code was issued for, and the code
is refused when that firmware was withdrawn, is no longer external, or its external URL
changed or left the allow-list. So the endpoint cannot be used as an open redirect.

Privacy: one aggregate counter per link and day (no IP, user agent, cookie or referrer),
so the redirect needs no consent. HEAD, link previews, crawlers and prefetches are
counted separately and never as clicks.
"""

from __future__ import annotations

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_safe

from .shortener import is_bot, record_click, resolve_short_code


@require_safe
def short_download(request: HttpRequest, code: str) -> HttpResponse:
    resolved = resolve_short_code(code)
    if resolved is None:
        raise Http404("Unknown download link.")
    link, firmware = resolved
    record_click(link, bot=is_bot(request))
    target = reverse(
        "firmwares:firmware_download",
        kwargs={"firmware_type": link.firmware_type, "firmware_id": str(firmware.pk)},
    )
    response = HttpResponseRedirect(target)
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
