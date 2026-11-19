"""Validators, caching and HEAD handling shared by the generated public text/XML files."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from django.http import HttpRequest, HttpResponse
from django.utils.cache import get_conditional_response
from django.utils.http import http_date, quote_etag

# Short shared cache: fresh for five minutes, servable while revalidating for an hour.
CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=3600"
LONG_CACHE_CONTROL = "public, max-age=86400, stale-while-revalidate=604800"


def etag_of(body: bytes, salt: str = "") -> str:
    return hashlib.sha256(salt.encode() + body).hexdigest()[:32]


def _timestamp(value: datetime | date | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day).astimezone()
    return int(value.timestamp())


def conditional(
    request: HttpRequest,
    body: bytes,
    *,
    content_type: str,
    last_modified: datetime | date | None = None,
    cache_control: str = CACHE_CONTROL,
    etag_salt: str = "",
    extra_headers: dict[str, str] | None = None,
) -> HttpResponse:
    """200 with validators, 304 for a matching conditional GET, bodiless for HEAD."""
    etag = quote_etag(etag_of(body, etag_salt))
    stamp = _timestamp(last_modified)
    response = get_conditional_response(request, etag=etag, last_modified=stamp)
    if response is None:
        response = HttpResponse(b"" if request.method == "HEAD" else body)
        response["Content-Type"] = content_type
        response["Content-Length"] = str(len(body))
    response["ETag"] = etag
    if stamp is not None:
        response["Last-Modified"] = http_date(stamp)
    response["Cache-Control"] = cache_control
    for name, value in (extra_headers or {}).items():
        response[name] = value
    return response
