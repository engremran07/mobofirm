"""IndexNow submission through ``SafeFetcher``.

IndexNow notifies participating search engines (Bing, Yandex, Seznam, Naver...) of
changed URLs. Google does not participate; a successful submission never guarantees
indexing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import fetcher as fetcher_module
from .models import SEOSettings
from .siteurls import site_origin

ENDPOINT = "https://api.indexnow.org/indexnow"
ENDPOINT_HOSTS = frozenset({"api.indexnow.org"})
MAX_URLS = 10_000
RETRYABLE = frozenset({429, 500, 502, 503, 504})


class IndexNowError(Exception):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class IndexNowResult:
    submitted: int
    status: int


def submit(urls: list[str]) -> IndexNowResult:
    """POST the URLs of this site to IndexNow; raises :class:`IndexNowError`."""
    row = SEOSettings.get_solo()
    if not (row.seo_enabled and row.indexnow_enabled and row.indexnow_key):
        raise IndexNowError("IndexNow is not enabled.")
    origin = site_origin()
    if not origin:
        raise IndexNowError("SITE_URL must be configured to submit URLs.")
    host = urlsplit(origin).netloc
    owned = list(
        dict.fromkeys(url for url in urls if urlsplit(url).netloc.lower() == host)
    )[:MAX_URLS]
    if not owned:
        raise IndexNowError("No URLs on this site to submit.")
    payload = {
        "host": host,
        "key": row.indexnow_key,
        "keyLocation": f"{origin}/{row.indexnow_key}.txt",
        "urlList": owned,
    }
    try:
        response = fetcher_module.default_fetcher().fetch(
            ENDPOINT,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
            body=json.dumps(payload).encode("utf-8"),
            follow_redirects=False,
            allowed_hosts=ENDPOINT_HOSTS,
        )
    except fetcher_module.BlockedDestination as error:
        raise IndexNowError(str(error)) from error
    except fetcher_module.FetchError as error:
        raise IndexNowError(str(error), retryable=True) from error
    if response.status in {200, 202}:
        return IndexNowResult(submitted=len(owned), status=response.status)
    raise IndexNowError(
        f"IndexNow rejected the submission with HTTP {response.status}.",
        retryable=response.status in RETRYABLE,
    )
