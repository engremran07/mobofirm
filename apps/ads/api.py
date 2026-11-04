from __future__ import annotations

"""
Public API surface for the Ads app.
Resolved dynamically via AppService to avoid hard imports.
"""

import logging  # noqa: E402
from typing import Any  # noqa: E402

from apps.ads.models import AdsSettings  # noqa: E402

logger = logging.getLogger(__name__)


def get_settings() -> dict[str, Any]:
    """
    Return a safe snapshot of ads configuration for use via AppService.
    Fail closed with explicit logging if settings cannot be loaded.
    """
    try:
        s = AdsSettings.get_solo()
    except Exception as exc:
        logger.error(
            "AdsSettings.get_solo failed; falling back to safe defaults",
            exc_info=True,
            extra={"error": str(exc)},
        )
        return {
            "ads_enabled": False,
            "affiliate_enabled": False,
            "ad_networks_enabled": False,
            "ad_aggressiveness_level": "balanced",
            "auto_ads_enabled": False,
            "rewarded_ads_enabled": False,
        }

    return {
        # Master controls
        "ads_enabled": bool(getattr(s, "ads_enabled", False)),
        "affiliate_enabled": bool(getattr(s, "affiliate_enabled", False)),
        "ad_networks_enabled": bool(getattr(s, "ad_networks_enabled", False)),
        "ad_aggressiveness_level": getattr(s, "ad_aggressiveness_level", "balanced"),
        # Auto ads
        "auto_ads_enabled": bool(getattr(s, "auto_ads_enabled", False)),
        "auto_ads_in_article": bool(getattr(s, "auto_ads_in_article", True)),
        "auto_ads_in_feed": bool(getattr(s, "auto_ads_in_feed", True)),
        "auto_ads_anchor": bool(getattr(s, "auto_ads_anchor", False)),
        "auto_ads_vignette": bool(getattr(s, "auto_ads_vignette", False)),
        # Rewarded ads
        "rewarded_ads_enabled": bool(getattr(s, "rewarded_ads_enabled", False)),
        "rewarded_ads_reward_type": getattr(s, "rewarded_ads_reward_type", "downloads"),
        "rewarded_ads_reward_value": getattr(s, "rewarded_ads_reward_value", 1),
        # Networks
        "adsense_enabled": bool(getattr(s, "adsense_enabled", False)),
        "adsense_publisher_id": getattr(s, "adsense_publisher_id", ""),
        "adsense_auto_ads": bool(getattr(s, "adsense_auto_ads", False)),
        "enabled_networks": s.get_enabled_networks()
        if hasattr(s, "get_enabled_networks")
        else [],
        # Header bidding
        "header_bidding_enabled": bool(getattr(s, "header_bidding_enabled", False)),
        "prebid_timeout_ms": getattr(s, "prebid_timeout_ms", 1000),
        # Native ads
        "native_ads_enabled": bool(getattr(s, "native_ads_enabled", False)),
        "native_ads_style": getattr(s, "native_ads_style", "card"),
        # AI optimization
        "ai_optimization_enabled": bool(getattr(s, "ai_optimization_enabled", False)),
        # Privacy
        "require_consent": bool(getattr(s, "require_consent", True)),
        "show_ads_without_consent": bool(getattr(s, "show_ads_without_consent", True)),
        # Limits
        "max_ads_per_page": getattr(s, "max_ads_per_page", 5),
        "lazy_load_ads": bool(getattr(s, "lazy_load_ads", True)),
    }


def should_show_ads(user=None, page_url: str = "") -> bool:
    """
    Check if ads should be shown for a given user and page.
    """
    try:
        s = AdsSettings.get_solo()
        return s.should_show_ads(user=user, page_url=page_url)
    except Exception:
        return False


def get_enabled_networks() -> list[str]:
    """
    Get list of enabled ad network identifiers.
    """
    try:
        s = AdsSettings.get_solo()
        return s.get_enabled_networks()
    except Exception:
        return []


def get_rewarded_ad_config(config_name: str | None = None) -> dict[str, Any]:
    """
    Get rewarded ad configuration for a specific config or the first enabled one.
    """
    try:
        from apps.ads.models import RewardedAdConfig

        if config_name:
            config = RewardedAdConfig.objects.filter(
                name=config_name, is_enabled=True
            ).first()
        else:
            config = RewardedAdConfig.objects.filter(is_enabled=True).first()

        if not config:
            return {"enabled": False}

        return {
            "enabled": True,
            "name": config.name,
            "reward_type": config.reward_type,
            "reward_amount": config.reward_amount,
            "reward_description": config.reward_description,
            "cooldown_minutes": config.cooldown_minutes,
            "daily_limit": config.daily_limit_per_user,
        }
    except Exception:
        return {"enabled": False}


def get_affiliate_products_settings() -> dict[str, Any]:
    """
    Get affiliate products configuration.
    """
    try:
        s = AdsSettings.get_solo()
        return {
            "enabled": bool(getattr(s, "affiliate_products_enabled", False)),
            "max_per_page": getattr(s, "affiliate_products_max_per_page", 4),
            "cache_hours": getattr(s, "affiliate_products_cache_hours", 6),
            "auto_fetch": bool(getattr(s, "affiliate_products_auto_fetch", True)),
            "show_on_firmware": bool(
                getattr(s, "affiliate_products_show_on_firmware", True)
            ),
            "show_on_model": bool(getattr(s, "affiliate_products_show_on_model", True)),
            "show_on_brand": bool(getattr(s, "affiliate_products_show_on_brand", True)),
            "show_on_blog": bool(getattr(s, "affiliate_products_show_on_blog", True)),
            "amazon_enabled": bool(getattr(s, "amazon_paapi_enabled", False)),
        }
    except Exception:
        return {"enabled": False}


def track_affiliate_click_sync(
    product_id: int,
    user_id: int | None = None,
    page_url: str = "",
    referrer_type: str = "",
    referrer_id: int | None = None,
    ip_address: str = "",
    user_agent: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """
    Track affiliate click synchronously.
    For use in views that need immediate response.
    """
    try:
        from django.contrib.auth import get_user_model

        from apps.ads.models import AffiliateClick, AffiliateProduct

        User = get_user_model()

        product = AffiliateProduct.objects.filter(id=product_id).first()
        if not product:
            return {"status": "product_not_found"}

        user = None
        if user_id:
            user = User.objects.filter(id=user_id).first()

        # Create click record
        click = AffiliateClick.objects.create(
            product=product,
            provider=product.provider,
            user=user,
            session_id=session_id[:100] if session_id else "",
            ip_address=ip_address[:45] if ip_address else "",
            user_agent=user_agent[:500] if user_agent else "",
            page_url=page_url[:500] if page_url else "",
        )

        # Increment product click count
        AffiliateProduct.objects.filter(id=product_id).update(clicks=product.clicks + 1)

        return {"status": "success", "click_id": click.pk}

    except Exception as exc:
        logger.exception(f"Failed to track affiliate click: {exc}")
        return {"status": "error", "error": str(exc)}


def get_contextual_products(
    brand=None, model=None, variant=None, blog_post=None, max_products: int = 4
) -> list[dict[str, Any]]:
    """
    Get contextual affiliate products for a given entity.
    Returns a list of product dictionaries.
    """
    try:
        from django.db.models import Q

        from apps.ads.models import AdsSettings, AffiliateProduct, AffiliateProductMatch

        settings_obj = AdsSettings.get_solo()
        if not settings_obj.affiliate_products_enabled:
            return []

        max_products = min(max_products, settings_obj.affiliate_products_max_per_page)

        products = []
        seen_ids = set()

        # 1. Manual matches
        match_filters = Q(is_hidden=False)
        if brand:
            match_filters &= Q(brand=brand)
        elif model:
            match_filters &= Q(model=model)
        elif variant:
            match_filters &= Q(variant=variant)
        elif blog_post:
            match_filters &= Q(blog_post=blog_post)

        matches = AffiliateProductMatch.objects.filter(
            match_filters,
            product__is_enabled=True,
            product__is_in_stock=True,
        ).select_related("product", "product__provider")[:max_products]

        for match in matches:
            if match.product_id not in seen_ids:  # type: ignore[attr-defined]
                products.append(_product_to_dict(match.product))
                seen_ids.add(match.product_id)  # type: ignore[attr-defined]

        if len(products) >= max_products:
            return products[:max_products]

        # 2. Targeted products
        remaining = max_products - len(products)
        targeted_filters = Q(is_enabled=True, is_in_stock=True)
        targeted_filters &= ~Q(id__in=seen_ids)

        if brand:
            targeted_filters &= Q(target_brands=brand)
        elif model:
            targeted_filters &= Q(target_models=model)

        targeted = AffiliateProduct.objects.filter(targeted_filters)[:remaining]

        for product in targeted:
            products.append(_product_to_dict(product))

        if len(products) >= max_products:
            return products[:max_products]

        # 3. Universal products
        remaining = max_products - len(products)
        universal = AffiliateProduct.objects.filter(
            is_enabled=True,
            is_in_stock=True,
            is_universal=True,
        ).exclude(id__in=seen_ids)[:remaining]

        for product in universal:
            products.append(_product_to_dict(product))

        return products[:max_products]

    except Exception as exc:
        logger.exception(f"Failed to get contextual products: {exc}")
        return []


def _product_to_dict(product) -> dict[str, Any]:
    """Convert affiliate product to dictionary."""
    return {
        "id": product.id,
        "name": product.name,
        "slug": product.slug,
        "product_type": product.product_type,
        "price": float(product.price),
        "sale_price": float(product.sale_price) if product.sale_price else None,
        "currency": product.currency,
        "image_url": product.image_url,
        "thumbnail_url": product.thumbnail_url,
        "product_url": product.product_url,
        "affiliate_url": product.affiliate_url,
        "rating": float(product.rating),
        "review_count": product.review_count,
        "provider_name": product.provider.name if product.provider else "",
        "discount_percent": product.get_discount_percent(),
    }


__all__ = [
    "get_affiliate_products_settings",
    "get_contextual_products",
    "get_enabled_networks",
    "get_rewarded_ad_config",
    "get_settings",
    "should_show_ads",
    "track_affiliate_click_sync",
]
