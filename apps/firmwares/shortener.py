"""External download link shortener (owner: ``firmwares``).

Public contract (also exported through ``apps.firmwares.api``)::

    shorten_download_url(firmware, *, request=None, user=None) -> str
    shorten_download(firmware, *, request=None, user=None) -> ShortenResult
    should_shorten(user, firmware) -> bool        # the audience gate (package perk)

What is shortened: the *download page* of an approved external firmware (its gate:
countdown, token, optional ad interstitial). The raw file-host URL is never shared and
never sent to a provider, and the final redirect after the gate is unchanged.

* ``first_party``: ``/d/<code>/`` (see ``shortener_views``).
* ``third_party``: the provider shortens the download page URL.
* ``both``: the provider shortens the first-party URL.

Transaction and failure boundaries (each step is its own small write, no HTTP call is
made inside a transaction this module opens):

* first-party code: one savepointed ``get_or_create`` keyed by the idempotency key
  ``(firmware type, firmware id, sha256(external URL))``; a code collision retries with
  a new code up to ``CODE_ATTEMPTS`` times, then the caller gets the plain page URL.
* provider result: cached under ``sha256(kind, endpoint, method, epoch, target)`` with a
  race-safe ``get_or_create``; a failed call caches nothing.
* the circuit breaker opens after ``BREAKER_THRESHOLD`` consecutive failures for
  ``BREAKER_OPEN_SECONDS``; while open, or on any provider failure, the share falls back
  to the first-party URL (``both``) or the plain download page URL (``third_party``).
* every created/failed/circuit-opened/health-check outcome is one audit row without
  secrets; the table keeps its newest ``MAX_OUTCOME_ROWS`` rows.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from apps.site_settings.services import absolute_url

from .access import has_ad_unlock, has_perk
from .catalog import (
    PUBLIC_FIRMWARE_TYPES,
    TYPE_KEY_BY_CLASS,
    get_public_firmware_queryset,
)
from .shortener_constants import (
    BREAKER_OPEN_SECONDS,
    BREAKER_THRESHOLD,
    CODE_ALPHABET,
    CODE_ATTEMPTS,
    CODE_LENGTH,
    MAX_OUTCOME_ROWS,
    Result,
    ShortenerMode,
    ShortenerOverride,
    Stage,
)
from .shortener_models import (
    DownloadShortLink,
    DownloadShortLinkDay,
    FirmwareShortenerOverride,
    ShortenerOutcome,
    ShortenerSettings,
    ThirdPartyShortLink,
)
from .shortener_providers import (
    Fetcher,
    ProviderConfig,
    ProviderError,
    shorten,
)
from .sources import validate_external_url

logger = logging.getLogger(__name__)

CODE_PATTERN = re.compile(rf"[{CODE_ALPHABET}]{{{CODE_LENGTH}}}")
BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|preview|fetch|monitor|facebookexternalhit|embedly|"
    r"whatsapp|telegram|discord|slack|skype|vkshare|pinterest|linkedin|mastodon|"
    r"bluesky|headless|python-requests|curl/|wget|go-http-client|java/|okhttp|"
    r"libwww|httpclient|scrapy|axios|node-fetch|lighthouse|pingdom|uptime",
    re.IGNORECASE,
)
FIRST_PARTY_MODES = frozenset({ShortenerMode.FIRST_PARTY, ShortenerMode.BOTH})
THIRD_PARTY_MODES = frozenset({ShortenerMode.THIRD_PARTY, ShortenerMode.BOTH})


class ShortenerError(Exception):
    """A first-party link could not be made; ``code`` is stable and safe to store."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ShortenResult:
    """The link to share. ``shortened`` is False when ``url`` is the plain page URL."""

    url: str
    mode: str
    shortened: bool = False
    first_party: bool = False
    third_party: bool = False
    fallback: str = ""


@dataclass(frozen=True)
class HealthOutcome:
    ok: bool
    code: str
    message: str


# ------------------------------------------------------------ eligibility ----


def firmware_type_key(firmware: Any) -> str:
    """The URL type key (``official``, ``engineering``, ...) of a firmware row."""
    return TYPE_KEY_BY_CLASS.get(type(firmware), "")


def is_shortenable(firmware: Any) -> bool:
    """Approved, active external firmware whose URL passes the allow-list."""
    if not (
        getattr(firmware, "is_external", False)
        and firmware.is_verified
        and firmware.is_active
        and firmware_type_key(firmware)
    ):
        return False
    try:
        validate_external_url(firmware.external_url)
    except ValidationError:
        return False
    return True


def target_digest(external_url: str) -> str:
    return hashlib.sha256(external_url.encode("utf-8")).hexdigest()


def resolve_mode(firmware: Any, settings_row: ShortenerSettings | None = None) -> str:
    """Per-firmware override, else per-brand choice, else the global mode."""
    key = firmware_type_key(firmware)
    override = (
        FirmwareShortenerOverride.objects.filter(
            firmware_type=key, firmware_id=firmware.pk
        )
        .values_list("mode", flat=True)
        .first()
    )
    if override:
        return str(override)
    brand = getattr(firmware, "brand", None)
    brand_mode = getattr(brand, "shortener_mode", ShortenerOverride.INHERIT)
    if brand_mode != ShortenerOverride.INHERIT:
        return str(brand_mode)
    return str((settings_row or ShortenerSettings.get_solo()).mode)


def download_page_url(firmware: Any, request: HttpRequest | None = None) -> str:
    path = reverse(
        "firmwares:firmware_download",
        kwargs={
            "firmware_type": firmware_type_key(firmware),
            "firmware_id": str(firmware.pk),
        },
    )
    return absolute_url(path, request)


# ------------------------------------------------------------ first party ----


def new_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def first_party_url(link: DownloadShortLink, request: HttpRequest | None = None) -> str:
    return absolute_url(
        reverse("firmware_short:go", kwargs={"code": link.code}), request
    )


def ensure_first_party_link(firmware: Any) -> DownloadShortLink:
    """The one short link of this firmware's current external URL (idempotent)."""
    key = firmware_type_key(firmware)
    identity = {
        "firmware_type": key,
        "firmware_id": firmware.pk,
        "target_digest": target_digest(firmware.external_url),
    }
    existing = DownloadShortLink.objects.filter(**identity).first()
    if existing is not None:
        return existing
    for _attempt in range(CODE_ATTEMPTS):
        try:
            with transaction.atomic():
                created_link, _created = DownloadShortLink.objects.get_or_create(
                    **identity, defaults={"code": new_code()}
                )
        except IntegrityError:
            # Either the new code collided or a concurrent request made this link.
            concurrent = DownloadShortLink.objects.filter(**identity).first()
            if concurrent is None:
                continue
            return concurrent
        return created_link
    raise ShortenerError("code_space_exhausted")


def resolve_short_code(code: str) -> tuple[DownloadShortLink, Any] | None:
    """The link and its live firmware, or ``None`` (forged, stale or withdrawn code).

    The destination is derived from the approved firmware row and must still match the
    URL the code was issued for, so a code can never lead anywhere new.
    """
    if not CODE_PATTERN.fullmatch(code or ""):
        return None
    link = DownloadShortLink.objects.filter(code=code).first()
    if link is None:
        return None
    model_class = dict(PUBLIC_FIRMWARE_TYPES).get(link.firmware_type)
    if model_class is None:
        return None
    firmware = (
        get_public_firmware_queryset(model_class)
        .select_related("brand")
        .filter(pk=link.firmware_id)
        .first()
    )
    if firmware is None or not is_shortenable(firmware):
        return None
    if target_digest(firmware.external_url) != link.target_digest:
        return None
    return link, firmware


def is_bot(request: HttpRequest) -> bool:
    """HEAD, link previews, crawlers, scripts and browser prefetches are not clicks."""
    agent = request.headers.get("User-Agent", "")
    purpose = (
        request.headers.get("Sec-Purpose", "") + request.headers.get("Purpose", "")
    ).lower()
    return (
        request.method == "HEAD"
        or not agent
        or bool(BOT_PATTERN.search(agent))
        or "prefetch" in purpose
        or "prerender" in purpose
        or request.headers.get("X-Moz", "").lower() == "prefetch"
    )


def record_click(
    link: DownloadShortLink, *, bot: bool, day: date | None = None
) -> None:
    """Count one click for the day. Stores nothing about the visitor."""
    moment = day or timezone.now().date()
    column = "bot_clicks" if bot else "clicks"
    with transaction.atomic():
        row, _created = DownloadShortLinkDay.objects.get_or_create(
            link=link, day=moment
        )
        DownloadShortLinkDay.objects.filter(pk=row.pk).update(**{column: F(column) + 1})


# ------------------------------------------------------------------ audit ----


def _host(url: str) -> str:
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def record_outcome(
    stage: str,
    result: str,
    *,
    provider: str = "",
    code: str = "",
    detail: str = "",
    firmware: Any = None,
    target: str = "",
    latency_ms: int = 0,
) -> None:
    """Write one audit row (stable codes and hosts only) and trim the table."""
    ShortenerOutcome.objects.create(
        stage=stage,
        result=result,
        provider_kind=provider,
        code=code[:40],
        detail=detail[:200],
        firmware_type=firmware_type_key(firmware) if firmware is not None else "",
        firmware_id=getattr(firmware, "pk", None),
        target_host=_host(target),
        latency_ms=latency_ms,
    )
    if ShortenerOutcome.objects.count() > MAX_OUTCOME_ROWS:
        stale = list(
            ShortenerOutcome.objects.order_by("-created_at", "-id").values_list(
                "pk", flat=True
            )[MAX_OUTCOME_ROWS:]
        )
        ShortenerOutcome.objects.filter(pk__in=stale).delete()


# ------------------------------------------------------------ third party ----


def provider_config(row: ShortenerSettings) -> ProviderConfig | None:
    """The adapter configuration with the decrypted key, or ``None`` when unusable."""
    if row.configuration_problem:
        return None
    try:
        key = row.get_api_key()
    except (InvalidToken, ImproperlyConfigured):
        return None
    return ProviderConfig(
        kind=row.provider_kind,
        endpoint=row.provider_endpoint,
        method=row.provider_method,
        api_key=key,
        auth_header=row.provider_auth_header,
        auth_prefix=row.provider_auth_prefix,
        body_template=row.provider_body_template,
        response_path=row.provider_response_path,
        timeout=row.timeout_seconds,
    )


def cache_key(row: ShortenerSettings, target: str) -> str:
    """Idempotency key: the same provider setup and target give the same link."""
    material = "\n".join(
        (
            row.provider_kind,
            row.provider_endpoint,
            row.provider_method,
            str(row.cache_epoch),
            target,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def breaker_is_open(row: ShortenerSettings, now: datetime) -> bool:
    return row.breaker_open_until is not None and row.breaker_open_until > now


def _reset_breaker(row: ShortenerSettings) -> None:
    if row.breaker_failures or row.breaker_open_until:
        ShortenerSettings.objects.filter(pk=row.pk).update(
            breaker_failures=0, breaker_open_until=None
        )


def _count_failure(row: ShortenerSettings, now: datetime) -> bool:
    """Count one failure; open the circuit at the threshold. True when it opened."""
    ShortenerSettings.objects.filter(pk=row.pk).update(
        breaker_failures=F("breaker_failures") + 1
    )
    failures = ShortenerSettings.objects.values_list("breaker_failures", flat=True).get(
        pk=row.pk
    )
    if failures < BREAKER_THRESHOLD:
        return False
    ShortenerSettings.objects.filter(pk=row.pk).update(
        breaker_open_until=now + timedelta(seconds=BREAKER_OPEN_SECONDS)
    )
    return True


def third_party_url(
    target: str,
    *,
    row: ShortenerSettings,
    firmware: Any = None,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """``(short_url, '')`` or ``('', reason)``; never raises for provider failures."""
    moment = now or timezone.now()
    config = provider_config(row)
    if config is None:
        return "", "misconfigured"
    key = cache_key(row, target)
    cached = (
        ThirdPartyShortLink.objects.filter(cache_key=key)
        .values_list("short_url", flat=True)
        .first()
    )
    if cached:
        return str(cached), ""
    if breaker_is_open(row, moment):
        return "", "circuit_open"
    started = time.monotonic()
    try:
        short = shorten(config, target, fetcher)
    except ProviderError as error:
        latency = int((time.monotonic() - started) * 1000)
        logger.warning("Link shortener %s failed: %s", row.provider_kind, error.code)
        record_outcome(
            Stage.THIRD_PARTY,
            Result.FAILED,
            provider=row.provider_kind,
            code=error.code,
            firmware=firmware,
            target=target,
            latency_ms=latency,
        )
        if _count_failure(row, moment):
            record_outcome(
                Stage.THIRD_PARTY,
                Result.BREAKER_OPEN,
                provider=row.provider_kind,
                code="circuit_opened",
                detail=f"Paused for {BREAKER_OPEN_SECONDS // 60} minutes.",
            )
        return "", error.code
    latency = int((time.monotonic() - started) * 1000)
    link, created = ThirdPartyShortLink.objects.get_or_create(
        cache_key=key,
        defaults={
            "provider_kind": row.provider_kind,
            "target_url": target[:500],
            "short_url": short,
        },
    )
    _reset_breaker(row)
    if created:
        record_outcome(
            Stage.THIRD_PARTY,
            Result.CREATED,
            provider=row.provider_kind,
            firmware=firmware,
            target=target,
            latency_ms=latency,
        )
    return link.short_url, ""


# ------------------------------------------------------------- audience gate ----

DIRECT_LINKS_PERK = "direct_links"


def should_shorten(user: Any, firmware: Any) -> bool:
    """Whether ``user`` is routed through the shortener for ``firmware``.

    THE gate hook (the only place that decides who sees shortened links). The shop
    package model decides: the free tier, anonymous visitors and any package without the
    ``direct_links`` perk are shortened; a package with ``direct_links`` (Silver, Gold,
    Platinum) gets the plain, gated download link. ``ad_free`` alone does not exempt a
    user. The perk is read through the shop entitlement contract
    (``access.has_perk`` -> ``apps.shop.entitlements``, optional): without the shop
    everybody is treated as the free tier.

    ``firmware`` is part of the signature on purpose: per-firmware or per-package unlock
    rules belong in this function and nowhere else. One rule lives here today: a file the
    viewer unlocked by watching rewarded ads (an unused ``ad_unlock`` ticket) is a direct,
    plain link, because the viewer already paid for it with ads. Callers must not
    duplicate the check.
    """
    if has_ad_unlock(user, firmware):
        return False
    return not has_perk(user, DIRECT_LINKS_PERK)


def _viewer(request: HttpRequest | None, user: Any) -> Any:
    return user if user is not None else getattr(request, "user", None)


# ------------------------------------------------------------- public API ----


def shorten_download(
    firmware: Any,
    *,
    request: HttpRequest | None = None,
    user: Any = None,
    fetcher: Fetcher | None = None,
    now: datetime | None = None,
) -> ShortenResult:
    """The link to share for an external download, per the effective mode and viewer.

    ``user`` defaults to ``request.user`` (anonymous without a request). Never raises for
    a provider or code-space failure: the result then carries the fallback link
    (first-party, else the plain download page) and a ``fallback`` code.
    """
    page = download_page_url(firmware, request)
    off = ShortenResult(url=page, mode=ShortenerMode.OFF)
    if not page or not is_shortenable(firmware):
        return off
    row = ShortenerSettings.get_solo()
    mode = resolve_mode(firmware, row)
    if mode == ShortenerMode.OFF or not should_shorten(
        _viewer(request, user), firmware
    ):
        return off

    url, first, third, fallback = page, False, False, ""
    if mode in FIRST_PARTY_MODES:
        try:
            url = first_party_url(ensure_first_party_link(firmware), request)
            first = True
        except ShortenerError as error:
            fallback = error.code
            record_outcome(
                Stage.FIRST_PARTY, Result.FAILED, code=error.code, firmware=firmware
            )
    if mode in THIRD_PARTY_MODES:
        short, reason = third_party_url(
            url, row=row, firmware=firmware, fetcher=fetcher, now=now
        )
        if short:
            url, third = short, True
        else:
            fallback = fallback or reason
    return ShortenResult(
        url=url,
        mode=mode,
        shortened=first or third,
        first_party=first,
        third_party=third,
        fallback=fallback,
    )


def shorten_download_url(
    firmware: Any, *, request: HttpRequest | None = None, user: Any = None
) -> str:
    """Contract for other apps: the link to share for ``firmware``'s download.

    Returns the plain download page URL when shortening is off, not applicable (hosted
    file, unapproved firmware) or the viewer's package grants direct links, so the
    result is always safe to share. ``user`` defaults to ``request.user``; without
    either the viewer is anonymous (shortened).
    """
    return shorten_download(firmware, request=request, user=user).url


def share_url_for(
    firmware: Any, default_url: str, *, request: HttpRequest | None = None
) -> str:
    """``default_url`` unless an external download link is shortened for sharing."""
    result = shorten_download(firmware, request=request)
    return result.url if result.shortened else default_url


# ----------------------------------------------------------- health check ----


def check_provider(
    *, fetcher: Fetcher | None = None, now: datetime | None = None
) -> HealthOutcome:
    """Ask the provider to shorten this site's home page (ignores cache and breaker)."""
    moment = now or timezone.now()
    row = ShortenerSettings.get_solo()
    probe = absolute_url("/")
    outcome: HealthOutcome
    config = provider_config(row)
    if config is None:
        problem = row.configuration_problem or "The stored API key cannot be read."
        outcome = HealthOutcome(False, "misconfigured", problem)
    elif not probe:
        outcome = HealthOutcome(
            False, "no_site_origin", "The site address is not configured."
        )
    else:
        started = time.monotonic()
        try:
            short = shorten(config, probe, fetcher)
        except ProviderError as error:
            outcome = HealthOutcome(
                False, error.code, f"The provider call failed ({error.code})."
            )
        else:
            _reset_breaker(row)
            outcome = HealthOutcome(
                True,
                "ok",
                f"The provider answered with a valid link on {_host(short)}.",
            )
        record_outcome(
            Stage.HEALTH_CHECK,
            Result.OK if outcome.ok else Result.FAILED,
            provider=row.provider_kind,
            code="" if outcome.ok else outcome.code,
            target=probe,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    ShortenerSettings.objects.filter(pk=row.pk).update(
        last_check_at=moment,
        last_check_ok=outcome.ok,
        last_check_message=outcome.message[:200],
    )
    return outcome
