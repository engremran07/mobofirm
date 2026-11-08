from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from django.contrib.auth.decorators import user_passes_test
from django.db import DatabaseError
from django.db.models import Count, Q
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from apps.ads import rewarded
from apps.ads.permissions import can_manage_ads as _can_manage_ads
from apps.ads.services import overview
from apps.ads.services.analytics.tracker import record_event as tracker_record_event
from apps.ads.services.markup import safe_click_url
from apps.ads.services.rotation.engine import choose_creative
from apps.ads.services.schemas import AdRequest, AdResponse, CreativeSelection
from apps.ads.services.targeting.engine import campaign_allowed
from apps.consent.constants import ADVERTISING
from apps.consent.services import has_consent
from apps.core.admin_pages import render_admin_page
from apps.core.utils.ip import get_client_ip
from apps.users.services.consent import hash_ip, hash_ua
from apps.users.services.rate_limit import allow_action

from .models import (
    AdCreative,
    AdEvent,
    AdPlacement,
    AdsSettings,
    AffiliateSource,
    Campaign,
    PlacementAssignment,
    RewardedAdConfig,
)

logger = logging.getLogger(__name__)


def _ads_settings() -> dict:
    settings = AdsSettings.get_solo()
    return {
        "ads_enabled": settings.ads_enabled,
        "affiliate_enabled": settings.affiliate_enabled,
        "ad_networks_enabled": settings.ad_networks_enabled,
        "ad_aggressiveness_level": settings.ad_aggressiveness_level,
    }


def _ads_enabled() -> bool:
    return bool(_ads_settings()["ads_enabled"])


def _has_ads_consent(request: HttpRequest) -> bool:
    """Ad measurement and personalisation need the visitor's advertising consent."""
    return has_consent(request, ADVERTISING)


def _log(request: HttpRequest, message: str, **extra):
    cid = getattr(request, "correlation_id", None)
    payload = {"correlation_id": cid, **extra}
    logger.info(message, extra={"event": payload})


def _is_rate_limited(key: str, limit: int) -> bool:
    """Atomic per-minute counter shared across workers (see rate_limit service)."""
    return not allow_action(key, max_attempts=limit, window_seconds=60)


@require_GET
def list_placements(request: HttpRequest) -> JsonResponse:
    if not _ads_enabled():
        return JsonResponse({"items": []})
    placements = AdPlacement.objects.filter(
        is_enabled=True, is_active=True, is_deleted=False
    )
    data = [
        {
            "name": p.name,
            "slug": p.slug,
            "code": p.code,
            "allowed_types": p.allowed_types,
            "allowed_sizes": p.allowed_sizes,
            "page_context": p.context or p.page_context,
            "template_reference": p.template_reference,
        }
        for p in placements
    ]
    _log(request, "ads.list_placements", count=len(data))
    return JsonResponse({"items": data})


@csrf_protect
@require_POST
def record_event(request: HttpRequest) -> JsonResponse:
    if not _ads_enabled():
        return JsonResponse({"ok": False, "error": "ads_disabled"}, status=403)
    if not _has_ads_consent(request):
        return JsonResponse({"ok": True, "skipped": "no_consent"})
    event_type = (request.POST.get("event_type") or "").strip()
    placement_slug = (request.POST.get("placement") or "").strip()
    campaign_id = request.POST.get("campaign")
    creative_id = request.POST.get("creative")
    page_url = request.POST.get("page_url", "")
    referrer = request.POST.get("referrer", "")
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    # Consent was verified server-side above; never trust a client-supplied flag.
    consent_granted = True
    if not placement_slug or event_type not in {"impression", "click"}:
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
    if page_url and not page_url.startswith(("http://", "https://")):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
    # Basic abuse throttle per IP
    rl_key = f"ads:event:{get_client_ip(request) or 'anon'}"
    if _is_rate_limited(rl_key, 50):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    hashed_ip = hash_ip(get_client_ip(request))
    hashed_ua = hash_ua(user_agent)
    placement = AdPlacement.objects.filter(
        slug=placement_slug, is_enabled=True, is_active=True, is_deleted=False
    ).first()
    if placement is None:
        return JsonResponse({"ok": False, "error": "invalid_placement"}, status=400)
    campaign = Campaign.objects.filter(pk=campaign_id).first() if campaign_id else None
    creative = (
        AdCreative.objects.filter(
            pk=creative_id, is_enabled=True, is_active=True, is_deleted=False
        ).first()
        if creative_id
        else None
    )
    if creative:
        assigned = placement.assignments.filter(
            creative=creative, is_enabled=True, is_active=True, is_deleted=False
        ).exists()
        if not assigned:
            return JsonResponse({"ok": False, "error": "invalid_mapping"}, status=400)
        # The creative owns its campaign; a client-supplied campaign must match.
        if campaign is not None and campaign.pk != creative.campaign_id:
            return JsonResponse({"ok": False, "error": "invalid_mapping"}, status=400)
        campaign = creative.campaign
    if campaign and not campaign_allowed(campaign, {"consent_ads": consent_granted}):
        return JsonResponse({"ok": False, "error": "campaign_not_allowed"}, status=400)
    tracker_record_event(
        event_type,
        placement=placement,
        creative=creative,
        campaign=campaign,
        user=request.user if request.user.is_authenticated else None,
        request_meta={
            "ip": hashed_ip,
            "referrer": referrer,
            "user_agent": hashed_ua,
            "page_url": page_url,
            "consent_granted": consent_granted,
            "correlation_id": getattr(request, "correlation_id", None),
        },
    )
    _log(
        request,
        "ads_event_recorded",
        event_type=event_type,
        placement=placement_slug,
        campaign=str(campaign_id or ""),
        page_url=page_url,
    )
    return JsonResponse({"ok": True})


@require_GET
def fill_ad(request: HttpRequest) -> JsonResponse:
    """
    Returns a creative for a placement slug using rotation engine.
    """
    if not _ads_enabled():
        return JsonResponse({"ok": False, "error": "ads_disabled"}, status=403)
    if not _has_ads_consent(request):
        return JsonResponse({"ok": True, "skipped": "no_consent"})
    slug = request.GET.get("placement") or ""
    page_url = request.GET.get("page_url", "")
    if page_url and not page_url.startswith(("http://", "https://")):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
    if not slug:
        return JsonResponse({"ok": False, "error": "missing_placement"}, status=400)
    if _is_rate_limited(f"ads:fill:{get_client_ip(request) or 'anon'}", 120):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    placement = AdPlacement.objects.filter(
        slug=slug, is_enabled=True, is_active=True, is_deleted=False
    ).first()
    if not placement:
        return JsonResponse({"ok": False, "error": "placement_not_found"}, status=404)
    ad_request = AdRequest(
        placement_code=placement.code,
        page_url=page_url,
        referrer=request.META.get("HTTP_REFERER", ""),
        user_id=request.user.pk if request.user.is_authenticated else None,
        session_id=request.session.session_key,
        consent_ads=_has_ads_consent(request),
        device=hash_ua(request.META.get("HTTP_USER_AGENT", "")),
        context={"ip": hash_ip(get_client_ip(request) or "")},
    )
    tags = (
        set((request.GET.get("tags") or "").split(","))
        if request.GET.get("tags")
        else set()
    )
    context = {
        "consent_ads": _has_ads_consent(request),
        "page_context": placement.context
        or placement.page_context
        or request.GET.get("page_context"),
        "tags": tags,
    }
    creative: AdCreative | None = choose_creative(placement, context)
    if not creative:
        return JsonResponse({"ok": False, "error": "no_creative"}, status=404)
    ad_response = AdResponse(
        placement_code=placement.code,
        creatives=[
            CreativeSelection(
                creative_id=creative.pk,
                campaign_id=getattr(creative, "campaign_id", None),
                weight=1,
            )
        ],
        tracking={
            "consent": ad_request.consent_ads,
            "correlation_id": getattr(request, "correlation_id", ""),
        },
    )
    _log(
        request,
        "ads_fill",
        placement=slug,
        creative=str(getattr(creative, "id", "")),
        campaign=str(getattr(creative, "campaign_id", "")),
        page_url=page_url,
    )
    payload = {
        "type": creative.creative_type,
        "html": creative.html,
        "image_url": creative.image_url,
        "click_url": creative.click_url,
        "campaign": creative.campaign_id,
        "placement": placement.slug,
        "creative": creative.pk,
        "page_url": page_url,
        "tracking": ad_response.tracking,
    }
    if _has_ads_consent(request):
        tracker_record_event(
            "impression",
            placement=placement,
            creative=creative,
            campaign=creative.campaign,
            user=request.user if request.user.is_authenticated else None,
            request_meta={
                "ip": hash_ip(get_client_ip(request) or ""),
                "referrer": request.META.get("HTTP_REFERER", ""),
                "user_agent": hash_ua(request.META.get("HTTP_USER_AGENT", "")),
                "page_url": request.GET.get("page_url", ""),
                "consent_granted": True,
                "correlation_id": getattr(request, "correlation_id", None),
            },
        )
    else:
        logger.info(
            "Ads impression skipped due to missing consent",
            extra={"placement": placement.slug},
        )
    return JsonResponse({"ok": True, "creative": payload})


@csrf_protect
@require_POST
def record_click(request: HttpRequest) -> JsonResponse:
    if not _ads_enabled():
        return JsonResponse({"ok": False, "error": "ads_disabled"}, status=403)
    if not _has_ads_consent(request):
        return JsonResponse({"ok": True, "skipped": "no_consent"})
    creative_id = (request.POST.get("creative") or "").strip()
    placement_slug = (request.POST.get("placement") or "").strip()
    if not creative_id or not placement_slug:
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
    page_url = request.POST.get("page_url", "")
    referrer = request.POST.get("referrer", "")
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    if page_url and not page_url.startswith(("http://", "https://")):
        return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
    # Basic abuse throttle per IP
    rl_key = f"ads:click:{get_client_ip(request) or 'anon'}"
    if _is_rate_limited(rl_key, 30):
        return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)
    placement = AdPlacement.objects.filter(
        slug=placement_slug, is_enabled=True, is_active=True, is_deleted=False
    ).first()
    if placement is None:
        return JsonResponse({"ok": False, "error": "invalid_placement"}, status=400)
    if not creative_id.isdecimal():
        return JsonResponse({"ok": False, "error": "invalid_creative"}, status=400)
    creative = AdCreative.objects.filter(
        pk=int(creative_id), is_enabled=True, is_active=True, is_deleted=False
    ).first()
    if creative is None:
        return JsonResponse({"ok": False, "error": "invalid_creative"}, status=400)
    assigned = placement.assignments.filter(
        creative=creative, is_enabled=True, is_active=True
    ).exists()
    if not assigned:
        return JsonResponse({"ok": False, "error": "invalid_mapping"}, status=400)
    if creative.campaign and not creative.campaign.is_live():
        return JsonResponse({"ok": False, "error": "campaign_not_live"}, status=400)
    tracker_record_event(
        "click",
        placement=placement,
        creative=creative,
        campaign=creative.campaign,
        user=request.user if request.user.is_authenticated else None,
        request_meta={
            "ip": hash_ip(get_client_ip(request) or ""),
            "referrer": referrer,
            "user_agent": hash_ua(user_agent),
            "page_url": page_url,
            "consent_granted": True,
            "correlation_id": getattr(request, "correlation_id", None),
        },
    )
    return JsonResponse({"ok": True})


@require_GET
def click_redirect(
    request: HttpRequest, placement: str, creative_id: int
) -> HttpResponse:
    """Count a banner click (with advertising consent) and forward to the advertiser.

    The destination always comes from the stored creative of an enabled placement, never
    from the request, so this cannot be used as an open redirect.
    """
    slot = AdPlacement.objects.filter(
        slug=placement, is_enabled=True, is_active=True, is_deleted=False
    ).first()
    creative = (
        AdCreative.objects.filter(
            pk=creative_id, is_enabled=True, is_active=True, is_deleted=False
        )
        .select_related("campaign")
        .first()
    )
    if slot is None or creative is None:
        raise Http404("Unknown ad.")
    assigned = (
        slot.fallback_creative_id == creative.pk
        or slot.assignments.filter(
            creative=creative, is_enabled=True, is_active=True, is_deleted=False
        ).exists()
    )
    destination = safe_click_url(creative.click_url)
    if not assigned or not destination or not creative.campaign.is_live():
        raise Http404("Unknown ad.")
    if (
        _ads_enabled()
        and _has_ads_consent(request)
        and not _is_rate_limited(f"ads:click:{get_client_ip(request) or 'anon'}", 30)
    ):
        tracker_record_event(
            "click",
            placement=slot,
            creative=creative,
            campaign=creative.campaign,
            user=request.user if request.user.is_authenticated else None,
            request_meta={
                "ip": hash_ip(get_client_ip(request) or ""),
                "referrer": request.headers.get("Referer", ""),
                "user_agent": hash_ua(request.headers.get("User-Agent", "")),
                "page_url": request.headers.get("Referer", ""),
                "consent_granted": True,
                "correlation_id": getattr(request, "correlation_id", None),
            },
        )
    response = HttpResponseRedirect(destination)
    response["Cache-Control"] = "no-store"
    return response


def _rewarded_overview() -> dict[str, Any]:
    """Rewarded-ads status for the dashboard: configured or an honest 'not configured'."""
    settings_row = AdsSettings.get_solo()
    configs = list(
        RewardedAdConfig.objects.filter(is_deleted=False).select_related("placement")
    )
    rows = [
        {
            "config": config,
            "reason": rewarded.unavailable_reason(config),
        }
        for config in configs
    ]
    return {
        "switch_on": bool(
            settings_row.ads_enabled and settings_row.rewarded_ads_enabled
        ),
        "rows": rows,
        "configured": any(not row["reason"] for row in rows),
        "stats": rewarded.analytics(),
    }


@user_passes_test(_can_manage_ads)
def dashboard(request: HttpRequest) -> HttpResponse:
    # Basic CTR stats
    impressions = AdEvent.objects.filter(event_type="impression").count()
    clicks = AdEvent.objects.filter(event_type="click").count()
    ctr = (clicks / impressions * 100) if impressions else 0
    ss = _ads_settings()
    placements = list(
        AdPlacement.objects.filter(is_deleted=False).order_by("zone", "name")[:50]
    )
    campaigns = list(Campaign.objects.filter(is_deleted=False)[:50])
    creatives = list(
        AdCreative.objects.filter(is_deleted=False).select_related("campaign")[:50]
    )
    affiliate_sources = AffiliateSource.objects.all()[:10]
    # Fill stats per placement / creative
    placement_stats = {}
    creative_stats = {}
    # OPTIMIZED: Single query with aggregation for placement stats
    placement_ids = [p.pk for p in placements]
    placement_aggregates = (
        AdEvent.objects.filter(placement_id__in=placement_ids)
        .values("placement_id")
        .annotate(
            impressions=Count("id", filter=Q(event_type="impression")),
            clicks=Count("id", filter=Q(event_type="click")),
        )
    )
    placement_lookup = {stat["placement_id"]: stat for stat in placement_aggregates}

    for p in placements:
        stats = placement_lookup.get(p.pk, {"impressions": 0, "clicks": 0})
        imp = stats["impressions"]
        clk = stats["clicks"]
        ctr_local = (clk / imp * 100) if imp else 0
        placement_stats[p.pk] = {
            "impressions": imp,
            "clicks": clk,
            "ctr": round(ctr_local, 2),
        }
    # OPTIMIZED: Single query with aggregation instead of N+1
    creative_ids = [c.pk for c in creatives]
    creative_aggregates = (
        AdEvent.objects.filter(creative_id__in=creative_ids)
        .values("creative_id")
        .annotate(
            impressions=Count("id", filter=Q(event_type="impression")),
            clicks=Count("id", filter=Q(event_type="click")),
        )
    )
    creative_lookup = {stat["creative_id"]: stat for stat in creative_aggregates}

    for c in creatives:
        creative_row = creative_lookup.get(c.pk, {"impressions": 0, "clicks": 0})
        imp = creative_row["impressions"]
        clk = creative_row["clicks"]
        ctr_local = (clk / imp * 100) if imp else 0
        creative_stats[c.pk] = {
            "impressions": imp,
            "clicks": clk,
            "ctr": round(ctr_local, 2),
        }
    assignments = PlacementAssignment.objects.filter(
        placement_id__in=placement_ids, is_enabled=True, is_deleted=False
    ).select_related("placement", "creative", "creative__campaign")
    placement_campaigns: defaultdict[int, list[dict[str, Any]]] = defaultdict(list)
    for a in assignments:
        placement_campaigns[a.placement_id].append(
            {
                "creative": a.creative.name,
                "campaign": getattr(a.creative.campaign, "name", ""),
                "locked": a.locked,
                "weight": a.weight,
                "active": a.creative.is_enabled and a.creative.campaign.is_live()
                if a.creative.campaign
                else a.creative.is_enabled,
            }
        )
    return render_admin_page(
        request,
        "ads/dashboard.html",
        title="Ad management",
        subtitle="Serving status, placements, campaigns and rewarded ads.",
        context={
            "ads_tab": "dashboard",
            "overview": overview.overview(),
            "profile": overview.profile_state(request),
            "rewarded": _rewarded_overview(),
            "placement_rows": [
                {
                    "placement": p,
                    "stats": placement_stats[p.pk],
                    "creatives": placement_campaigns.get(p.pk, []),
                }
                for p in placements
            ],
            "creative_rows": [
                {"creative": c, "stats": creative_stats[c.pk]} for c in creatives
            ],
            "campaigns": campaigns,
            "ads_enabled": _ads_enabled(),
            "ad_networks_enabled": ss.get("ad_networks_enabled", False),
            "affiliate_enabled": ss.get("affiliate_enabled", False),
            "impressions": impressions,
            "clicks": clicks,
            "ctr": round(ctr, 2),
            "ad_aggressiveness_level": ss.get("ad_aggressiveness_level", "balanced"),
            "affiliate_sources": affiliate_sources,
            "can_place": request.user.has_perm("ads.change_adplacement"),
        },
    )


@csrf_protect
@user_passes_test(
    lambda u: (
        getattr(u, "is_staff", False)
        or getattr(u, "is_superuser", False)
        or getattr(u, "has_role", lambda *r: False)("admin", "editor")
    )
)
@require_POST
def toggle_settings(request: HttpRequest) -> HttpResponse:
    ss = AdsSettings.get_solo()
    action = request.POST.get("action", "")
    placement_id = request.POST.get("placement")
    campaign_id = request.POST.get("campaign")

    if action in ("disable_placement", "enable_placement") and placement_id:
        placement = AdPlacement.objects.filter(pk=placement_id).first()
        if placement:
            placement.is_enabled = action == "enable_placement"
            placement.save(update_fields=["is_enabled"])
    elif action == "toggle_lock" and placement_id:
        placement = AdPlacement.objects.filter(pk=placement_id).first()
        if placement:
            placement.locked = not placement.locked
            placement.save(update_fields=["locked"])
    elif action in ("disable_campaign", "enable_campaign") and campaign_id:
        campaign = Campaign.objects.filter(pk=campaign_id).first()
        if campaign:
            campaign.is_active = action == "enable_campaign"
            campaign.save(update_fields=["is_active"])
    elif action == "toggle_campaign_lock" and campaign_id:
        campaign = Campaign.objects.filter(pk=campaign_id).first()
        if campaign:
            campaign.locked = not campaign.locked
            campaign.save(update_fields=["locked"])
    else:
        # Only the global settings form (no action) may change site-wide ad state;
        # an unknown action or a missing target id must never switch ads off.
        if action not in ("", "update_settings") or "ads_enabled" not in request.POST:
            return HttpResponseBadRequest("Unknown or incomplete ads action.")
        ss.ads_enabled = request.POST.get("ads_enabled") == "1"
        ss.ad_networks_enabled = request.POST.get("ad_networks_enabled") == "1"
        ss.affiliate_enabled = request.POST.get("affiliate_enabled") == "1"
        level = (
            request.POST.get("ad_aggressiveness_level") or ss.ad_aggressiveness_level
        )
        if level in ("minimal", "balanced", "aggressive"):
            ss.ad_aggressiveness_level = level
        ss.save()
    _log(
        request,
        "ads.dashboard.toggle",
        action=action,
        placement=str(placement_id or ""),
        campaign=str(campaign_id or ""),
    )
    return redirect("ads:dashboard")


# ==================== AFFILIATE PRODUCTS ====================


@csrf_protect
@require_POST
def track_affiliate_click(request: HttpRequest) -> JsonResponse:
    """
    Track affiliate product click for analytics.
    Called via beacon or AJAX when user clicks an affiliate product link.
    """
    import json

    if not _ads_enabled() or not _has_ads_consent(request):
        return JsonResponse({"ok": True, "skipped": "no_consent"})

    try:
        if request.content_type == "application/json":
            data = json.loads(request.body)
        else:
            data = request.POST

        product_id = data.get("product_id")
        if not product_id:
            return JsonResponse(
                {"ok": False, "error": "missing_product_id"}, status=400
            )

        # Rate limit per IP
        rl_key = f"affiliate:click:{get_client_ip(request) or 'anon'}"
        if _is_rate_limited(rl_key, 100):
            return JsonResponse({"ok": False, "error": "rate_limited"}, status=429)

        # Track the click
        from apps.ads.api import track_affiliate_click_sync

        try:
            product_id = int(product_id)
            referrer_id = int(data.get("referrer_id") or 0) or None
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
        if product_id <= 0 or (referrer_id is not None and referrer_id <= 0):
            return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
        page_url = data.get("page_url", "")
        referrer_type = data.get("referrer_type", "")
        if not isinstance(page_url, str) or not isinstance(referrer_type, str):
            return JsonResponse({"ok": False, "error": "bad_payload"}, status=400)
        result = track_affiliate_click_sync(
            product_id=product_id,
            user_id=request.user.pk if request.user.is_authenticated else None,
            page_url=page_url[:500],
            ip_address=get_client_ip(request) or "",
            user_agent=request.META.get("HTTP_USER_AGENT", "")[:500],
            session_id=request.session.session_key or "",
        )

        if result.get("status") == "success":
            return JsonResponse({"ok": True, "click_id": result.get("click_id")})
        if result.get("error") == "tracking_unavailable":
            return JsonResponse(
                {"ok": False, "error": "tracking_unavailable"}, status=503
            )
        else:
            return JsonResponse(
                {"ok": False, "error": result.get("status")}, status=400
            )

    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)


@require_GET
def get_affiliate_products(request: HttpRequest) -> JsonResponse:
    """
    Get affiliate products for a given context.
    Used for client-side lazy loading of products.
    """
    from apps.ads.api import get_affiliate_products_settings, get_contextual_products

    settings = get_affiliate_products_settings()
    if settings.get("error"):
        return JsonResponse({"ok": False, "error": settings["error"]}, status=503)
    if not settings.get("enabled"):
        return JsonResponse({"ok": False, "products": []})

    # Get context parameters
    brand_id = request.GET.get("brand_id")
    model_id = request.GET.get("model_id")
    variant_id = request.GET.get("variant_id")
    post_id = request.GET.get("post_id")
    try:
        max_products = int(request.GET.get("max", 4))
        context_ids = {
            "brand": int(brand_id) if brand_id else None,
            "model": int(model_id) if model_id else None,
            "variant": int(variant_id) if variant_id else None,
            "post": int(post_id) if post_id else None,
        }
    except ValueError:
        return JsonResponse({"ok": False, "error": "bad_context"}, status=400)

    try:
        brand = model = variant = post = None

        if context_ids["brand"]:
            from apps.firmwares.models import Brand

            brand = Brand.objects.filter(id=context_ids["brand"]).first()
        if context_ids["model"]:
            from apps.firmwares.models import Model

            model = Model.objects.filter(id=context_ids["model"]).first()
        if context_ids["variant"]:
            from apps.firmwares.models import Variant

            variant = Variant.objects.filter(id=context_ids["variant"]).first()
        if context_ids["post"]:
            from apps.blog.models import Post

            post = Post.objects.filter(id=context_ids["post"]).first()

        products = get_contextual_products(
            brand=brand,
            model=model,
            variant=variant,
            blog_post=post,
            max_products=max_products,
        )
    except DatabaseError:
        logger.exception("Failed to load affiliate products.")
        return JsonResponse({"ok": False, "error": "products_unavailable"}, status=503)

    return JsonResponse(
        {
            "ok": True,
            "products": products,
            "count": len(products),
        }
    )
