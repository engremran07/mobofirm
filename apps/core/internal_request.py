"""Identity of the site's own in-process crawler.

The SEO self-crawl renders pages through Django's request handler (no network). Those
requests must look like a visitor to the rendering and authorization code, but must
never count as traffic: no view counters, ad impressions, referral clicks or any other
write that a person's visit would cause.

The identity is a WSGI *environ* entry, not an HTTP header. Servers copy request
headers into ``HTTP_*`` keys only, so a remote client cannot create this key; the value
must additionally equal a secret generated per process, so even a misconfigured proxy
that injected environ keys could not claim it. Counting code asks
:func:`is_internal_crawl` and stays silent when it is true.
"""

from __future__ import annotations

import hmac
import secrets

from django.http import HttpRequest

ENVIRON_KEY = "mobofirm.internal_crawl"

_PROCESS_SECRET = secrets.token_urlsafe(32)


def internal_crawl_environ() -> dict[str, str]:
    """Extra WSGI environ entries that mark a request as the in-process crawler."""
    return {ENVIRON_KEY: _PROCESS_SECRET}


def is_internal_crawl(request: HttpRequest) -> bool:
    """True only for requests issued by ``apps.seo.inprocess`` in this process."""
    value = request.META.get(ENVIRON_KEY)
    return isinstance(value, str) and hmac.compare_digest(value, _PROCESS_SECRET)
