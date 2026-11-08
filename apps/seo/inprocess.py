"""Self-crawl without a network: requests go through Django's own request handler.

When the site audits itself, an HTTP round trip to its own host is slow, blocked by the
private-network guard in development, and impossible from a single-threaded dev server.
``InProcessFetcher`` answers the crawler's ``fetch`` calls with the real URL conf,
middleware and templates, so the observations are the same HTML a visitor receives.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.test import Client

from . import fetcher as fetcher_module
from .fetcher import (
    REDIRECT_STATUSES,
    BlockedDestination,
    FetchError,
    FetchResult,
    SafeFetcher,
    TooManyRedirects,
)
from .inventory import serving_host

TRANSPORT_SETTING = "SEO_CRAWL_TRANSPORT"


@dataclass
class InProcessFetcher(SafeFetcher):
    """``SafeFetcher`` drop-in that serves same-site URLs in this process."""

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        follow_redirects: bool = True,
        allowed_hosts: frozenset[str] | None = None,
    ) -> FetchResult:
        method = method.upper()
        if method not in {"GET", "HEAD"}:
            raise FetchError(f"Method {method} is not supported in-process.")
        client = Client(raise_request_exception=False)
        started = time.monotonic()
        current = url
        chain: list[tuple[int, str, str]] = []
        for _hop in range(self.policy.max_redirects + 1):
            parts = urlsplit(current)
            hostname = (parts.hostname or "").lower()
            if allowed_hosts is not None and hostname not in allowed_hosts:
                raise BlockedDestination(f"{hostname} is not an allowed host.")
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            response = client.generic(
                method,
                target,
                secure=parts.scheme == "https",
                HTTP_HOST=serving_host(),
                HTTP_USER_AGENT=self.policy.user_agent,
            )
            reply = {key.lower(): value for key, value in response.items()}
            location = reply.get("location", "")
            if (
                follow_redirects
                and response.status_code in REDIRECT_STATUSES
                and location
            ):
                destination = urljoin(current, location)
                chain.append((response.status_code, current, destination))
                current = destination
                continue
            payload = self._payload(response, reply)
            return FetchResult(
                url=url,
                final_url=current,
                status=response.status_code,
                headers=reply,
                body=payload,
                redirects=tuple(chain),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        raise TooManyRedirects(
            f"More than {self.policy.max_redirects} redirects from {url}."
        )

    def _payload(self, response: object, reply: Mapping[str, str]) -> bytes:
        content_type = reply.get("content-type", "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith(
            self.policy.allowed_content_types
        ):
            return b""
        if getattr(response, "streaming", False):
            return b""
        return bytes(getattr(response, "content", b""))[: self.policy.max_bytes]


def self_crawl(origin: str) -> bool:
    """Whether ``origin`` is this deployment (crawl in-process) or an external host."""
    configured = str(getattr(settings, TRANSPORT_SETTING, "auto")).lower()
    if configured == "http":
        return False
    if configured == "inprocess":
        return True
    host = (urlsplit(origin).hostname or "").lower()
    allowed = {h.lower().lstrip(".") for h in settings.ALLOWED_HOSTS if h}
    return bool(host) and host in allowed


def choose_fetcher(origin: str) -> SafeFetcher:
    """In-process for the site itself, the hardened HTTP fetcher for anything else."""
    if self_crawl(origin):
        return InProcessFetcher(policy=fetcher_module.FetchPolicy.from_settings())
    return fetcher_module.default_fetcher()
