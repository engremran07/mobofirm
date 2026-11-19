"""Small public text files: security.txt, humans.txt, ads.txt and app-ads.txt.

Each file is served only when real configuration exists, so an unconfigured site
answers 404 rather than publishing placeholders.

* ``/.well-known/security.txt`` (RFC 9116): ``Contact``, ``Expires``, ``Policy`` and
  ``Canonical`` come from :class:`SEOSettings`; the file disappears once ``Expires``
  has passed (a stale file is worse than none).
* ``/humans.txt``: optional, off by default, body from settings.
* ``/ads.txt`` and ``/app-ads.txt`` (IAB Tech Lab): other apps contribute authorised
  seller lines through :func:`register_ads_lines` (an app that is not installed simply
  contributes nothing); ``apps.seo`` never imports the ads app.
* There is deliberately no ``llms.txt``: no search engine or crawler documents it as a
  ranking or inclusion signal (Google states it needs no special file), so it would
  be an unverifiable promise.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from django.urls import reverse
from django.utils import timezone

from .models import SEOSettings

AdsProvider = Callable[[], list[str]]
ADS_KINDS = ("ads.txt", "app-ads.txt")
_ads_providers: dict[str, list[AdsProvider]] = {kind: [] for kind in ADS_KINDS}


def register_ads_lines(kind: str, provider: AdsProvider) -> None:
    """Contribute authorised-seller lines (``domain, id, DIRECT|RESELLER[, tag]``)."""
    if kind not in _ads_providers:
        raise ValueError(f"Unknown ads file {kind!r}.")
    if provider not in _ads_providers[kind]:
        _ads_providers[kind].append(provider)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def security_txt(
    row: SEOSettings, origin: str, now: datetime | None = None
) -> str | None:
    """RFC 9116 body, or ``None`` when not configured or already expired."""
    contact = row.security_contact.strip()
    moment = now or timezone.now()
    if not (row.seo_enabled and contact and row.security_expires):
        return None
    if row.security_expires <= moment:
        return None
    target = contact if contact.startswith("https://") else f"mailto:{contact}"
    lines = [f"Contact: {target}", f"Expires: {_rfc3339(row.security_expires)}"]
    if row.security_policy_url:
        lines.append(f"Policy: {row.security_policy_url}")
    if origin:
        lines.append(f"Canonical: {origin}{reverse('seo_public:security_txt')}")
    return "\n".join(lines) + "\n"


def humans_txt(row: SEOSettings) -> str | None:
    body = row.humans_txt.strip()
    if not (row.seo_enabled and row.humans_txt_enabled and body):
        return None
    return body + "\n"


def _valid_ads_line(line: str) -> bool:
    fields = [part.strip() for part in line.split("#", 1)[0].split(",")]
    return (
        len(fields) in {3, 4}
        and all(fields[:3])
        and fields[2].upper() in {"DIRECT", "RESELLER"}
    )


def ads_txt(kind: str) -> str | None:
    """Body of ``ads.txt``/``app-ads.txt`` from registered providers, if any."""
    lines: list[str] = []
    for provider in _ads_providers.get(kind, []):
        lines.extend(line.strip() for line in provider() if _valid_ads_line(line))
    unique = list(dict.fromkeys(lines))
    return "\n".join(unique) + "\n" if unique else None
