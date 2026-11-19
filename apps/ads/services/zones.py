"""Runtime resolution of an ad zone: what, if anything, a ``{% ad_zone %}`` renders.

Order of decisions for a visitor: exclusion list -> placement for (template, zone) ->
placement switched on -> ads enabled for this user and page -> advertising consent ->
device targeting -> creative (rotation, caps) -> fallback creative. Any "no" renders
nothing, so an unconfigured or disabled site shows no ad space at all.

Staff with ``ads.change_adplacement`` can switch on the *test profile* (session toggle or
``?ad_preview=1``): every zone then renders a clearly marked test box instead, ignoring
the global switches and consent, and never recording an impression. The flag is only
honoured after the permission check, so it has no effect for any other visitor.
"""

from __future__ import annotations

import re
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Literal
from weakref import WeakKeyDictionary

from django.core import signing
from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse

from apps.ads.models import AdCreative, AdPlacement, AdsSettings, TemplateAdExclusion
from apps.ads.services import markup
from apps.ads.services.analytics.tracker import record_event
from apps.ads.services.rotation.engine import choose_creative
from apps.ads.services.targeting.engine import campaign_within_caps
from apps.ads.zones import (
    DEVICE_ALL,
    DEVICE_DESKTOP,
    DEVICE_MOBILE,
    ZONE_BY_CODE,
    Zone,
    reserved_size,
)
from apps.consent.constants import ADVERTISING
from apps.consent.services import has_consent
from apps.core.internal_request import is_internal_crawl
from apps.core.utils.ip import get_client_ip
from apps.users.services.consent import hash_ip, hash_ua

MANAGE_PERMISSION = "ads.change_adplacement"
PREVIEW_PARAM = "ad_preview"
PREVIEW_SESSION_KEY = "ads_test_profile_until"
PREVIEW_TTL_SECONDS = 3600
PREVIEW_TOKEN_SALT = "ads.preview-link"
PREVIEW_TOKEN_MAX_AGE = 600

# Pages that never carry ads, whatever a placement says (sign-in, errors, consent).
NEVER_TEMPLATE_PREFIXES = ("account/", "allauth/", "errors/", "admin/", "consent/")
# Account areas: a placement must name these templates explicitly, never "every page".
SITE_WIDE_DENY_PREFIXES = (
    "users/",
    "wallet/",
    "referral/",
    "devices/",
    "shop/",
    "pricing/",
)

_MOBILE_UA = re.compile(
    r"Mobi|iPhone|iPod|Windows Phone|BlackBerry|Opera Mini", re.IGNORECASE
)

Mode = Literal["live", "test", "paused", "unplaced"]


@dataclass(frozen=True, slots=True)
class ZoneRender:
    mode: Mode
    zone: Zone
    size: str
    placement: AdPlacement | None = None
    creative: AdCreative | None = None
    html: str = ""
    device: str = DEVICE_ALL


@dataclass(slots=True)
class _RequestZones:
    placements: list[AdPlacement]
    excluded: frozenset[str]
    # Creatives already shown on this page (an automatic slot never repeats one) and the
    # number of automatic slots rendered, against ``AdsSettings.max_ads_per_page``.
    used_creatives: set[int] = field(default_factory=set)
    auto_slots: int = 0
    auto_zone_counts: dict[str, int] = field(default_factory=dict)
    # ``auto_placement`` answers per (page, zone): a listing asks once per tile.
    auto_gate: dict[tuple[str, str], tuple[bool, AdPlacement | None]] = field(
        default_factory=dict
    )


_BY_REQUEST: WeakKeyDictionary[HttpRequest, _RequestZones] = WeakKeyDictionary()


def can_preview(request: HttpRequest) -> bool:
    user = getattr(request, "user", None)
    return bool(
        user is not None
        and user.is_authenticated
        and user.is_staff
        and user.has_perm(MANAGE_PERMISSION)
    )


def profile_enabled(request: HttpRequest) -> bool:
    """Whether the staff session toggle for the test profile is on and unexpired."""
    session = getattr(request, "session", None)
    if session is None:
        return False
    return float(session.get(PREVIEW_SESSION_KEY, 0) or 0) > time.time()


def set_test_profile(request: HttpRequest, enabled: bool) -> None:
    if enabled:
        request.session[PREVIEW_SESSION_KEY] = time.time() + PREVIEW_TTL_SECONDS
    else:
        request.session.pop(PREVIEW_SESSION_KEY, None)


def _token_user_id(request: HttpRequest) -> int | None:
    raw = request.GET.get(PREVIEW_PARAM, "")
    if not raw:
        return None
    try:
        data = signing.loads(
            raw, salt=PREVIEW_TOKEN_SALT, max_age=PREVIEW_TOKEN_MAX_AGE
        )
    except signing.BadSignature:
        return None
    user_id = data.get("u") if isinstance(data, dict) else None
    return user_id if isinstance(user_id, int) else None


def preview_url(user: Any, path: str) -> str:
    """``path`` with a short-lived signed flag that only ``user`` can use."""
    token = signing.dumps({"u": user.pk}, salt=PREVIEW_TOKEN_SALT)
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{PREVIEW_PARAM}={token}"


def is_preview(request: HttpRequest) -> bool:
    """Test ads for this request: only ever true for permitted staff.

    The flag is the staff session toggle, or the signed link issued by the placement
    manager to that same user. A copied link is useless to anyone else and expires.
    """
    if not can_preview(request):
        return False
    if profile_enabled(request):
        return True
    return _token_user_id(request) == request.user.pk


def is_mobile(request: HttpRequest) -> bool:
    hint = request.headers.get("Sec-CH-UA-Mobile", "").strip()
    if hint:
        return hint == "?1"
    return bool(_MOBILE_UA.search(request.headers.get("User-Agent", "")))


def device_matches(placement: AdPlacement, request: HttpRequest) -> bool:
    if placement.device_target == DEVICE_MOBILE:
        return is_mobile(request)
    if placement.device_target == DEVICE_DESKTOP:
        return not is_mobile(request)
    return True


def _state(request: HttpRequest) -> _RequestZones:
    cached = _BY_REQUEST.get(request)
    if cached is None:
        try:
            placements = list(
                AdPlacement.objects.filter(is_deleted=False)
                .exclude(zone="")
                .select_related("fallback_creative", "fallback_creative__campaign")
            )
            excluded = frozenset(
                TemplateAdExclusion.objects.values_list("template_path", flat=True)
            )
        except DatabaseError:
            placements, excluded = [], frozenset()
        cached = _RequestZones(placements, excluded)
        _BY_REQUEST[request] = cached
    return cached


def placement_for(
    placements: list[AdPlacement], template_name: str, zone_code: str
) -> AdPlacement | None:
    """The placement for a zone on a template: template-specific beats site-wide."""
    in_zone = [p for p in placements if p.zone == zone_code]
    for placement in in_zone:
        if template_name and placement.template_reference == template_name:
            return placement
    for placement in in_zone:
        if not placement.template_reference:
            return placement
    return None


def _site_allows(request: HttpRequest) -> bool:
    try:
        row = AdsSettings.get_solo()
    except DatabaseError:
        return False
    user = getattr(request, "user", None)
    return row.should_show_ads(user=user, page_url=request.path) and has_consent(
        request, ADVERTISING
    )


def _fallback(placement: AdPlacement) -> AdCreative | None:
    creative = placement.fallback_creative
    if (
        creative is None
        or creative.is_deleted
        or not (creative.is_enabled and creative.is_active)
    ):
        return None
    campaign = creative.campaign
    if campaign.is_deleted or not campaign.is_live():
        return None
    return creative if campaign_within_caps(campaign) else None


def _creative_for(
    placement: AdPlacement, exclude: Collection[int] = ()
) -> AdCreative | None:
    context = {
        "consent_ads": True,
        "page_context": placement.context or placement.page_context,
        "tags": set(),
    }
    chosen = choose_creative(placement, context, exclude=exclude)
    if chosen is not None:
        return chosen
    fallback = _fallback(placement)
    return fallback if fallback is not None and fallback.pk not in exclude else None


def click_url(placement: AdPlacement, creative: AdCreative) -> str:
    """Tracking redirect for a banner, empty when the creative has no valid target."""
    if not markup.safe_click_url(creative.click_url):
        return ""
    return reverse(
        "ads:click_redirect",
        kwargs={"placement": placement.slug, "creative_id": creative.pk},
    )


def _record_impression(
    request: HttpRequest, placement: AdPlacement, creative: AdCreative
) -> None:
    if is_internal_crawl(request):
        return
    user = getattr(request, "user", None)
    record_event(
        "impression",
        placement=placement,
        creative=creative,
        campaign=creative.campaign,
        user=user if user is not None and user.is_authenticated else None,
        request_meta={
            "ip": hash_ip(get_client_ip(request) or ""),
            "referrer": request.headers.get("Referer", ""),
            "user_agent": hash_ua(request.headers.get("User-Agent", "")),
            "page_url": request.build_absolute_uri(request.path),
            "consent_granted": True,
            "correlation_id": getattr(request, "correlation_id", None),
        },
    )


def _preview(zone: Zone, placement: AdPlacement | None) -> ZoneRender:
    if placement is None:
        return ZoneRender("unplaced", zone, zone.default_size)
    size = reserved_size(placement.allowed_sizes, zone.code)
    live = placement.is_enabled and placement.is_active
    mode: Mode = "test" if live else "paused"
    return ZoneRender(
        mode, zone, size, placement=placement, device=placement.device_target
    )


def _placement(
    state: _RequestZones, template_name: str, zone_code: str
) -> AdPlacement | None:
    """The placement for a zone on a page, after the site-wide account-area deny list."""
    placement = placement_for(state.placements, template_name, zone_code)
    if (
        placement is not None
        and not placement.template_reference
        and template_name.startswith(SITE_WIDE_DENY_PREFIXES)
    ):
        return None
    return placement


def zone_blocked(request: HttpRequest, template_name: str, zone_code: str) -> bool:
    """True when a zone can never render on this page (unknown, error/account page,
    excluded template)."""
    if zone_code not in ZONE_BY_CODE or template_name.startswith(
        NEVER_TEMPLATE_PREFIXES
    ):
        return True
    return template_name in _state(request).excluded


def auto_placement(
    request: HttpRequest, template_name: str, zone_code: str
) -> tuple[bool, AdPlacement | None]:
    """``(serving, placement)`` for an automatic zone, cheap enough to ask per tile.

    ``serving`` is False when nothing could render (blocked page, no live placement,
    ads off for this visitor, device mismatch), so callers skip all further work.
    Staff with the test profile always get ``True`` to see where slots would go. The
    answer is computed once per request, page and zone.
    """
    state = _state(request)
    key = (template_name, zone_code)
    if key not in state.auto_gate:
        state.auto_gate[key] = _auto_placement(request, template_name, zone_code)
    return state.auto_gate[key]


def _auto_placement(
    request: HttpRequest, template_name: str, zone_code: str
) -> tuple[bool, AdPlacement | None]:
    """Uncached :func:`auto_placement`."""
    if zone_blocked(request, template_name, zone_code):
        return False, None
    placement = _placement(_state(request), template_name, zone_code)
    if is_preview(request):
        return True, placement
    if placement is None or not (placement.is_enabled and placement.is_active):
        return False, None
    if not _site_allows(request) or not device_matches(placement, request):
        return False, None
    return True, placement


def _count_auto(state: _RequestZones, zone_code: str) -> None:
    state.auto_slots += 1
    state.auto_zone_counts[zone_code] = state.auto_zone_counts.get(zone_code, 0) + 1


def auto_used(request: HttpRequest, zone_code: str) -> int:
    """Automatic slots of this zone already rendered on the page."""
    return _state(request).auto_zone_counts.get(zone_code, 0)


def auto_budget(request: HttpRequest) -> int:
    """How many more automatic slots this page may carry (``max_ads_per_page``)."""
    try:
        limit = int(AdsSettings.get_solo().max_ads_per_page)
    except DatabaseError:
        limit = 0
    return max(0, limit - _state(request).auto_slots)


def min_words_between_ads() -> int:
    try:
        return int(AdsSettings.get_solo().min_content_between_ads)
    except DatabaseError:
        return 0


def resolve_zone(
    request: HttpRequest,
    template_name: str,
    zone_code: str,
    *,
    unique: bool = False,
) -> ZoneRender | None:
    """What a zone renders now. ``unique`` (automatic slots) never repeats a creative
    already shown on this page and counts toward ``max_ads_per_page``."""
    zone = ZONE_BY_CODE.get(zone_code)
    if zone is None or template_name.startswith(NEVER_TEMPLATE_PREFIXES):
        return None
    state = _state(request)
    if template_name in state.excluded:
        return None
    placement = _placement(state, template_name, zone_code)
    if is_preview(request):
        if unique:
            _count_auto(state, zone_code)
        return _preview(zone, placement)
    if placement is None or not (placement.is_enabled and placement.is_active):
        return None
    if not _site_allows(request) or not device_matches(placement, request):
        return None
    creative = _creative_for(placement, state.used_creatives if unique else frozenset())
    if creative is None:
        return None
    href = click_url(placement, creative)
    html = markup.render_creative(creative, click_href=href)
    if not html:
        return None
    _record_impression(request, placement, creative)
    if unique:
        state.used_creatives.add(creative.pk)
        _count_auto(state, zone_code)
    return ZoneRender(
        "live",
        zone,
        reserved_size(placement.allowed_sizes, zone_code),
        placement=placement,
        creative=creative,
        html=html,
        device=placement.device_target,
    )
