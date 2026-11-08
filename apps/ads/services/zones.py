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
from dataclasses import dataclass
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
SITE_WIDE_DENY_PREFIXES = ("users/", "wallet/", "referral/", "devices/", "shop/")

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


def _creative_for(placement: AdPlacement) -> AdCreative | None:
    context = {
        "consent_ads": True,
        "page_context": placement.context or placement.page_context,
        "tags": set(),
    }
    return choose_creative(placement, context) or _fallback(placement)


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


def resolve_zone(
    request: HttpRequest, template_name: str, zone_code: str
) -> ZoneRender | None:
    zone = ZONE_BY_CODE.get(zone_code)
    if zone is None or template_name.startswith(NEVER_TEMPLATE_PREFIXES):
        return None
    state = _state(request)
    if template_name in state.excluded:
        return None
    placement = placement_for(state.placements, template_name, zone_code)
    if (
        placement is not None
        and not placement.template_reference
        and template_name.startswith(SITE_WIDE_DENY_PREFIXES)
    ):
        placement = None
    if is_preview(request):
        return _preview(zone, placement)
    if placement is None or not (placement.is_enabled and placement.is_active):
        return None
    if not _site_allows(request) or not device_matches(placement, request):
        return None
    creative = _creative_for(placement)
    if creative is None:
        return None
    href = click_url(placement, creative)
    html = markup.render_creative(creative, click_href=href)
    if not html:
        return None
    _record_impression(request, placement, creative)
    return ZoneRender(
        "live",
        zone,
        reserved_size(placement.allowed_sizes, zone_code),
        placement=placement,
        creative=creative,
        html=html,
        device=placement.device_target,
    )
