from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

try:
    from celery import shared_task
except Exception:  # pragma: no cover - fallback when Celery not installed

    def shared_task(*dargs, **dkwargs):  # type: ignore[assignment]
        def decorator(func):
            return func

        if dargs and callable(dargs[0]):
            return dargs[0]
        return decorator


from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)


# ==================== EVENT AGGREGATION ====================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    soft_time_limit=120,
    time_limit=180,
)
def aggregate_events(self) -> dict:
    """
    Aggregate ad events in the background.
    Runs hourly to aggregate ad impressions, clicks, and conversions.
    """
    try:
        from apps.ads.models import AdEvent

        logger.info("Starting ad events aggregation")

        # Get events from last hour
        one_hour_ago = timezone.now() - timedelta(hours=1)

        events = (
            AdEvent.objects.filter(created_at__gte=one_hour_ago)
            .values("placement_id", "event_type")
            .annotate(count=Count("id"))
        )

        aggregated_count = 0

        for event_data in events:
            aggregated_count += 1
            logger.debug(
                "Placement %s: %s × %d",
                event_data["placement_id"],
                event_data["event_type"],
                event_data["count"],
            )

        logger.info("Aggregated %d ad event types", aggregated_count)

        return {
            "status": "success",
            "aggregated": aggregated_count,
            "period_start": one_hour_ago.isoformat(),
        }

    except Exception as exc:
        logger.error(f"Ad aggregation failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=60)  # noqa: B904


@shared_task(
    bind=True,
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def cleanup_old_events(self):
    """
    Cleanup ad events older than 90 days.
    Runs daily to keep database size manageable.
    """
    try:
        from apps.ads.models import AdEvent

        cutoff_date = timezone.now() - timedelta(days=90)

        deleted_count = AdEvent.objects.filter(created_at__lt=cutoff_date).delete()[0]

        logger.info(f"Deleted {deleted_count} old ad events")

        return {"status": "success", "deleted": deleted_count}

    except Exception as e:
        logger.error(f"Ad cleanup failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# ==================== BLOG POST CONTENT SCANNING ====================


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def scan_blog_post_content(self, post_id: int) -> dict[str, Any]:
    """
    Scan a published blog post's body HTML for in-content ad placement.

    Triggered automatically via ``post_save`` signal when a blog post is
    published.  Analyses paragraph count, headings, images, and determines
    optimal insertion points for inline / in-feed ads.
    """
    try:
        from apps.ads.services.scanner import scan_blog_post_body

        return scan_blog_post_body(post_id)
    except Exception as exc:
        logger.error("Blog post scan failed for post %s: %s", post_id, exc)
        raise self.retry(exc=exc, countdown=30)  # noqa: B904


# ==================== AI OPTIMIZATION TASKS ====================


@shared_task(
    bind=True,
    acks_late=True,
    soft_time_limit=600,
    time_limit=900,
)
def ai_optimize_ad_placements(self) -> dict[str, Any]:
    """
    Use AI to analyze ad performance and optimize placements.

    This task:
    1. Analyzes click-through rates, revenue, and user engagement
    2. Identifies underperforming placements
    3. Suggests placement adjustments
    4. Optionally auto-applies low-risk optimizations
    """
    try:
        from apps.ads.models import (
            AdEvent,
            AdPlacement,
            AdsSettings,
            PlacementAssignment,
        )
        from apps.ads.services.ai_optimizer import analyze_performance

        settings_obj = AdsSettings.get_solo()
        if not settings_obj.ai_optimization_enabled:
            return {"status": "skipped", "reason": "ai_optimization_disabled"}

        # Get performance metrics for last 7 days
        week_ago = timezone.now() - timedelta(days=7)

        placement_metrics = []
        for placement in AdPlacement.objects.filter(is_active=True, is_enabled=True):
            impressions = AdEvent.objects.filter(
                placement=placement, event_type="impression", created_at__gte=week_ago
            ).count()

            clicks = AdEvent.objects.filter(
                placement=placement, event_type="click", created_at__gte=week_ago
            ).count()

            ctr = (clicks / impressions * 100) if impressions > 0 else 0

            placement_metrics.append(
                {
                    "placement_id": placement.pk,
                    "placement_name": placement.name,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": ctr,
                }
            )

        # Get creative-level metrics
        creative_metrics = []
        for assignment in PlacementAssignment.objects.filter(
            is_active=True, is_enabled=True
        ).select_related("creative"):
            creative = assignment.creative
            impressions = AdEvent.objects.filter(
                creative=creative, event_type="impression", created_at__gte=week_ago
            ).count()

            clicks = AdEvent.objects.filter(
                creative=creative, event_type="click", created_at__gte=week_ago
            ).count()

            ctr = (clicks / impressions * 100) if impressions > 0 else 0

            creative_metrics.append(
                {
                    "creative_id": creative.id,
                    "ctr": ctr,
                    "impressions": impressions,
                }
            )

        # Run AI analysis
        suggestions = analyze_performance(creative_metrics)

        # Auto-apply safe optimizations if enabled
        applied = []
        if settings_obj.ai_optimize_creatives:
            for suggestion in suggestions:
                if suggestion.get("action") == "pause" and suggestion.get(
                    "creative_id"
                ):
                    # Pause underperforming creatives (safe operation)
                    from apps.ads.models import AdCreative

                    try:
                        creative = AdCreative.objects.get(
                            id=suggestion["creative_id"],
                            locked=False,  # Don't touch locked creatives
                        )
                        creative.is_enabled = False
                        creative.save(update_fields=["is_enabled"])
                        applied.append(
                            {
                                "creative_id": suggestion["creative_id"],
                                "action": "paused",
                                "reason": suggestion.get("reason", "Low CTR"),
                            }
                        )
                        logger.info(
                            f"AI paused creative {creative.pk}: {suggestion.get('reason')}"
                        )
                    except AdCreative.DoesNotExist:
                        pass

        return {
            "status": "success",
            "placement_metrics": placement_metrics,
            "suggestions": suggestions,
            "applied": applied,
        }

    except Exception as e:
        logger.error(f"AI optimization failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


# ==================== REWARDED ADS TASKS ====================


@shared_task(
    bind=True,
    acks_late=True,
    soft_time_limit=60,
    time_limit=120,
)
def process_rewarded_ad_completion(
    self,
    view_id: int,
    watch_duration: int,
) -> dict[str, Any]:
    """
    Process a completed rewarded ad view and grant the reward.

    Called when user finishes watching a rewarded video ad.
    """
    try:
        from apps.ads.models import RewardedAdView

        view = RewardedAdView.objects.select_related("config", "user").get(id=view_id)

        if view.reward_granted:
            return {"status": "already_granted"}

        config = view.config
        if not config:
            return {"status": "error", "reason": "no_config"}

        # Verify minimum watch time
        min_seconds = config.min_watch_seconds or 30  # Default 30s
        if watch_duration < min_seconds:
            view.status = "skipped"
            view.watch_duration_seconds = watch_duration
            view.save()
            return {"status": "skipped", "reason": "insufficient_watch_time"}

        # Check rate limits
        today = timezone.now().date()
        views_today = RewardedAdView.objects.filter(
            user=view.user, config=config, reward_granted=True, created_at__date=today
        ).count()

        if views_today >= config.daily_limit_per_user:
            return {"status": "limit_reached", "reason": "daily_limit"}

        # Grant the reward
        reward_type = config.reward_type
        reward_amount = config.reward_amount

        # Handle different reward types
        if reward_type == "download":
            # Grant extra download credits
            _grant_download_credits(view.user, reward_amount)
        elif reward_type == "points":
            # Grant points/credits
            _grant_points(view.user, reward_amount)
        elif reward_type == "premium":
            # Grant temporary premium access
            _grant_premium_access(view.user, hours=reward_amount)
        elif reward_type == "ad_free":
            # Grant ad-free period
            _grant_ad_free_period(view.user, hours=reward_amount)

        # Update view record
        view.status = "completed"
        view.completed_at = timezone.now()
        view.watch_duration_seconds = watch_duration
        view.reward_granted = True
        view.reward_type = reward_type
        view.reward_amount = reward_amount
        view.save()

        logger.info(
            f"Rewarded ad completed: user={view.user.id}, reward={reward_type}:{reward_amount}"
        )

        return {
            "status": "success",
            "reward_type": reward_type,
            "reward_amount": reward_amount,
        }

    except RewardedAdView.DoesNotExist:
        return {"status": "error", "reason": "view_not_found"}
    except Exception as e:
        logger.error(f"Rewarded ad processing failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def _grant_download_credits(user, amount: int):
    """Grant download credits to user."""
    # Implement based on your user model
    try:
        profile = user.profile if hasattr(user, "profile") else user
        if hasattr(profile, "download_credits"):
            profile.download_credits = (profile.download_credits or 0) + amount
            profile.save(update_fields=["download_credits"])
    except Exception as e:
        logger.warning(f"Could not grant download credits: {e}")


def _grant_points(user, amount: int):
    """Grant points/credits to user."""
    try:
        profile = user.profile if hasattr(user, "profile") else user
        if hasattr(profile, "points"):
            profile.points = (profile.points or 0) + amount
            profile.save(update_fields=["points"])
    except Exception as e:
        logger.warning(f"Could not grant points: {e}")


def _grant_premium_access(user, hours: int):
    """Grant temporary premium access."""
    try:
        profile = user.profile if hasattr(user, "profile") else user
        if hasattr(profile, "premium_until"):
            current = profile.premium_until or timezone.now()
            if current < timezone.now():
                current = timezone.now()
            profile.premium_until = current + timedelta(hours=hours)
            profile.save(update_fields=["premium_until"])
    except Exception as e:
        logger.warning(f"Could not grant premium access: {e}")


def _grant_ad_free_period(user, hours: int):
    """Grant ad-free viewing period."""
    try:
        profile = user.profile if hasattr(user, "profile") else user
        if hasattr(profile, "ad_free_until"):
            current = profile.ad_free_until or timezone.now()
            if current < timezone.now():
                current = timezone.now()
            profile.ad_free_until = current + timedelta(hours=hours)
            profile.save(update_fields=["ad_free_until"])
    except Exception as e:
        logger.warning(f"Could not grant ad-free period: {e}")


# ==================== AD NETWORK SYNC TASKS ====================


@shared_task(
    bind=True,
    acks_late=True,
    soft_time_limit=300,
    time_limit=600,
)
def sync_ad_networks(self) -> dict[str, Any]:
    """
    Sync ad network configurations and fetch latest ad unit data.

    For networks with APIs (AdSense, Ad Manager, etc.), this fetches
    the latest ad unit configurations, performance data, and settings.
    """
    try:
        from apps.ads.models import AdNetwork

        synced = []
        errors = []

        for network in AdNetwork.objects.filter(is_enabled=True):
            try:
                result = _sync_network(network)
                synced.append(
                    {
                        "network": network.name,
                        "status": result.get("status", "unknown"),
                    }
                )
                network.last_sync_at = timezone.now()
                network.sync_status = result.get("status", "synced")
                network.save(update_fields=["last_sync_at", "sync_status"])
            except Exception as e:
                errors.append(
                    {
                        "network": network.name,
                        "error": str(e),
                    }
                )
                network.sync_status = f"error: {str(e)[:100]}"
                network.save(update_fields=["sync_status"])

        return {
            "status": "success",
            "synced": synced,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"Ad network sync failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def _sync_network(network) -> dict[str, Any]:
    """Sync a specific ad network."""
    network_type = network.network_type

    if network_type == "adsense":
        return _sync_adsense(network)
    elif network_type == "admanager":
        return _sync_admanager(network)
    elif network_type in ("ezoic", "mediavine", "adthrive"):
        return _sync_premium_network(network)
    else:
        # Generic networks don't have APIs to sync
        return {"status": "no_api"}


def _sync_adsense(network) -> dict[str, Any]:
    """Sync Google AdSense data via API (if configured)."""
    # Placeholder for AdSense API integration
    # Would use google-api-python-client for real implementation
    return {"status": "adsense_api_not_configured"}


def _sync_admanager(network) -> dict[str, Any]:
    """Sync Google Ad Manager (DFP) data via API."""
    # Placeholder for Ad Manager API integration
    return {"status": "admanager_api_not_configured"}


def _sync_premium_network(network) -> dict[str, Any]:
    """Sync premium network data (Ezoic, Mediavine, AdThrive)."""
    # These networks typically provide dashboard access rather than APIs
    return {"status": "manual_sync_required"}


# ==================== SCHEDULED TASK REGISTRATION ====================


@shared_task(bind=True, soft_time_limit=30, time_limit=60)
def ads_hourly_tasks(self):
    """Run all hourly ads tasks."""
    aggregate_events.delay()  # type: ignore[attr-defined]


@shared_task(bind=True, soft_time_limit=30, time_limit=60)
def ads_daily_tasks(self):
    """Run all daily ads tasks."""
    cleanup_old_events.delay()  # type: ignore[attr-defined]
    ai_optimize_ad_placements.delay()  # type: ignore[attr-defined]
    sync_ad_networks.delay()  # type: ignore[attr-defined]
    sync_affiliate_products.delay()  # type: ignore[attr-defined]


# ==================== AFFILIATE PRODUCTS ====================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    acks_late=True,
    soft_time_limit=600,
    time_limit=900,
)
def sync_affiliate_products(self) -> dict[str, Any]:
    """
    Sync affiliate products from all enabled providers.
    Fetches new products based on brand/model keywords.
    """
    try:
        from apps.ads.models import AdsSettings, AffiliateProvider

        settings_obj = AdsSettings.get_solo()

        if not settings_obj.affiliate_products_enabled:
            logger.info("Affiliate products disabled, skipping sync")
            return {"status": "disabled"}

        results = {
            "providers_synced": 0,
            "products_added": 0,
            "products_updated": 0,
            "errors": [],
        }

        providers = AffiliateProvider.objects.filter(is_enabled=True)

        for provider in providers:
            try:
                if provider.provider_type == "amazon":
                    result = _sync_amazon_products(provider, settings_obj)
                elif provider.provider_type == "cj":
                    result = _sync_cj_products(provider, settings_obj)
                elif provider.provider_type == "shareasale":
                    result = _sync_shareasale_products(provider, settings_obj)
                else:
                    result = {
                        "status": "no_api",
                        "products_added": 0,
                        "products_updated": 0,
                    }

                results["providers_synced"] += 1
                results["products_added"] += result.get("products_added", 0)
                results["products_updated"] += result.get("products_updated", 0)

                # Update provider sync status
                provider.last_sync_at = timezone.now()
                provider.sync_status = "success"
                provider.save(update_fields=["last_sync_at", "sync_status"])

            except Exception as exc:
                logger.exception(f"Failed to sync {provider.name}: {exc}")
                results["errors"].append(f"{provider.name}: {exc!s}")
                provider.sync_status = f"error: {str(exc)[:100]}"
                provider.save(update_fields=["sync_status"])

        logger.info(f"Affiliate product sync complete: {results}")
        return results

    except Exception as exc:
        logger.error(f"Affiliate product sync failed: {exc}", exc_info=True)
        raise self.retry(exc=exc)  # noqa: B904


def _sync_amazon_products(provider, settings_obj) -> dict[str, Any]:
    """
    Sync products from Amazon Product Advertising API.

    Requires:
    - Amazon PA-API credentials in settings
    - paapi5-python-sdk package installed (pip install paapi5-python-sdk)
    """
    from apps.firmwares.models import Brand

    if not settings_obj.amazon_paapi_enabled:
        return {"status": "paapi_disabled", "products_added": 0, "products_updated": 0}

    access_key = settings_obj.amazon_paapi_access_key
    secret_key = settings_obj.amazon_paapi_secret_key
    partner_tag = settings_obj.amazon_paapi_partner_tag
    region = settings_obj.amazon_paapi_region
    marketplace = settings_obj.amazon_paapi_marketplace

    if not all([access_key, secret_key, partner_tag]):
        logger.warning("Amazon PA-API credentials not configured")
        return {
            "status": "credentials_missing",
            "products_added": 0,
            "products_updated": 0,
        }

    results = {"products_added": 0, "products_updated": 0}

    try:
        # Try to import Amazon PA-API SDK
        from paapi5_python_sdk.api.default_api import (  # type: ignore[import-not-found]
            DefaultApi,
        )
        from paapi5_python_sdk.models.partner_type import (  # type: ignore[import-not-found]
            PartnerType,
        )
        from paapi5_python_sdk.models.search_items_request import (  # type: ignore[import-not-found]
            SearchItemsRequest,
        )
        from paapi5_python_sdk.models.search_items_resource import (  # type: ignore[import-not-found]
            SearchItemsResource,
        )
        from paapi5_python_sdk.rest import (  # type: ignore[import-not-found]
            ApiException,
        )

        # Configure API client
        default_api = DefaultApi(
            access_key=access_key,
            secret_key=secret_key,
            host=f"webservices.{marketplace.replace('www.', '')}",
            region=region,
        )

        # Build search keywords from brands
        brands = Brand.objects.all()[:20]  # Limit to top 20 brands

        search_keywords = []
        for brand in brands:
            search_keywords.append(f"{brand.name} phone case")
            search_keywords.append(f"{brand.name} charger")
            search_keywords.append(f"{brand.name} screen protector")

        # Also use fallback keywords
        fallback_keywords = [
            kw.strip()
            for kw in settings_obj.affiliate_products_fallback_keywords.split(",")
            if kw.strip()
        ]
        search_keywords.extend(fallback_keywords)

        # Search for each keyword (limited to avoid rate limits)
        for keyword in search_keywords[:10]:  # Max 10 searches per sync
            try:
                search_request = SearchItemsRequest(
                    partner_tag=partner_tag,
                    partner_type=PartnerType.ASSOCIATES,
                    keywords=keyword,
                    search_index="Electronics",
                    item_count=10,
                    resources=[
                        SearchItemsResource.ITEMINFO_TITLE,
                        SearchItemsResource.ITEMINFO_FEATURES,
                        SearchItemsResource.OFFERS_LISTINGS_PRICE,
                        SearchItemsResource.IMAGES_PRIMARY_LARGE,
                        SearchItemsResource.CUSTOMERREVIEWS_STARRATING,
                        SearchItemsResource.CUSTOMERREVIEWS_COUNT,
                    ],
                )

                response = default_api.search_items(search_request)

                if response.search_result and response.search_result.items:
                    for item in response.search_result.items:
                        product_result = _save_amazon_product(item, provider, keyword)
                        if product_result == "created":
                            results["products_added"] += 1
                        elif product_result == "updated":
                            results["products_updated"] += 1

            except ApiException as api_exc:
                logger.warning(f"Amazon API error for '{keyword}': {api_exc}")
                continue
            except Exception as exc:
                logger.exception(f"Error searching '{keyword}': {exc}")
                continue

    except ImportError:
        logger.warning(
            "paapi5-python-sdk not installed. Install with: pip install paapi5-python-sdk"
        )
        return {
            "status": "sdk_not_installed",
            "products_added": 0,
            "products_updated": 0,
        }
    except Exception as exc:
        logger.exception(f"Amazon PA-API error: {exc}")
        return {
            "status": f"error: {str(exc)[:100]}",
            "products_added": 0,
            "products_updated": 0,
        }

    return results


def _save_amazon_product(item, provider, search_keyword) -> str:
    """Save or update an Amazon product from PA-API response."""
    from django.template.defaultfilters import slugify

    from apps.ads.models import AffiliateProduct

    try:
        asin = item.asin

        # Extract product data
        title = (
            item.item_info.title.display_value
            if item.item_info and item.item_info.title
            else f"Product {asin}"
        )

        # Price
        price = 0.0
        sale_price = None
        currency = "USD"

        if item.offers and item.offers.listings:
            listing = item.offers.listings[0]
            if listing.price:
                price = float(listing.price.amount or 0)
                currency = listing.price.currency or "USD"
            if listing.saving_basis:
                sale_price = price
                price = float(listing.saving_basis.amount or price)

        # Images
        image_url = ""
        thumbnail_url = ""
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url
            thumbnail_url = item.images.primary.large.url

        # Rating
        rating = 0.0
        review_count = 0
        if item.customer_reviews:
            if item.customer_reviews.star_rating:
                rating = float(item.customer_reviews.star_rating.value or 0)
            if item.customer_reviews.count:
                review_count = int(item.customer_reviews.count or 0)

        # Product URL
        product_url = item.detail_page_url or f"https://www.amazon.com/dp/{asin}"

        # Determine product type from title
        title_lower = title.lower()
        if "case" in title_lower:
            product_type = "case"
        elif "charger" in title_lower or "cable" in title_lower:
            product_type = "charger"
        elif "screen protector" in title_lower:
            product_type = "screen_protector"
        elif (
            "headphone" in title_lower
            or "earbud" in title_lower
            or "airpod" in title_lower
        ):
            product_type = "headphones"
        elif "battery" in title_lower:
            product_type = "battery"
        elif "watch" in title_lower:
            product_type = "smartwatch"
        else:
            product_type = "accessory"

        # Check if product exists
        existing = AffiliateProduct.objects.filter(
            provider=provider, external_id=asin
        ).first()

        if existing:
            # Update existing product
            existing.name = title[:255]
            existing.price = price
            existing.sale_price = sale_price
            existing.currency = currency
            existing.image_url = image_url
            existing.thumbnail_url = thumbnail_url
            existing.rating = rating
            existing.review_count = review_count
            existing.product_url = product_url
            existing.affiliate_url = product_url  # PA-API URLs include tracking
            existing.price_updated_at = timezone.now()
            existing.is_in_stock = True
            existing.save()
            return "updated"
        else:
            # Create new product
            AffiliateProduct.objects.create(
                provider=provider,
                name=title[:255],
                slug=slugify(title[:250]),
                description="",
                product_type=product_type,
                external_id=asin,
                asin=asin,
                price=price,
                sale_price=sale_price,
                currency=currency,
                image_url=image_url,
                thumbnail_url=thumbnail_url,
                product_url=product_url,
                affiliate_url=product_url,
                rating=rating,
                review_count=review_count,
                is_enabled=True,
                is_in_stock=True,
                price_updated_at=timezone.now(),
                target_keywords=search_keyword,
                api_data={"asin": asin, "search_keyword": search_keyword},
            )
            return "created"

    except Exception as exc:
        logger.exception(f"Error saving Amazon product: {exc}")
        return "error"


def _sync_cj_products(provider, settings_obj) -> dict[str, Any]:
    """
    Sync products from CJ (Commission Junction) Affiliate Publisher API v2.

    Uses the Product Catalog API endpoint to fetch affiliate products,
    then creates/updates AffiliateProduct records.

    API Docs: https://developers.cj.com/en-us/docs/cj-api/docs/reference-link-search
    Endpoint: https://product-search.api.cj.com/v2/product-search
    """
    try:
        import requests
    except ImportError:
        logger.warning("requests library not available for CJ sync")
        return {
            "status": "requests_not_installed",
            "products_added": 0,
            "products_updated": 0,
        }

    if not provider or not hasattr(provider, "api_key"):
        logger.warning("CJ provider missing API key")
        return {
            "status": "missing_credentials",
            "products_added": 0,
            "products_updated": 0,
        }

    try:
        from apps.ads.models import AffiliateProduct

        api_key = provider.api_key
        base_url = "https://product-search.api.cj.com/v2/product-search"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

        products_added = 0
        products_updated = 0
        error_count = 0

        # CJ API pagination — fetch multiple pages
        page_size = 100
        for page in range(1, 11):  # Fetch up to 1000 products (10 pages)
            params = {
                "limit": page_size,
                "offset": (page - 1) * page_size,
            }

            try:
                resp = requests.get(
                    base_url, headers=headers, params=params, timeout=30
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error("CJ API request failed: %s", e)
                error_count += 1
                if error_count > 2:
                    break
                continue

            products = data.get("products", [])
            if not products:
                break

            for product_data in products:
                try:
                    cj_id = product_data.get("advertiser_id")
                    name = product_data.get("name", "")
                    url = product_data.get("click_url", "")
                    commission_percent = Decimal(
                        str(product_data.get("commission_percent", 0))
                    )

                    if not name or not cj_id:
                        continue

                    aff_prod, created = AffiliateProduct.objects.update_or_create(
                        provider=provider,
                        external_id=str(cj_id),
                        defaults={
                            "name": name,
                            "url": url,
                            "commission_rate": commission_percent,
                            "is_enabled": True,
                            "api_data": {
                                "cj_advertiser_id": cj_id,
                                "synced_at": timezone.now().isoformat(),
                            },
                        },
                    )

                    if created:
                        products_added += 1
                    else:
                        products_updated += 1

                except Exception as e:
                    logger.error("Error processing CJ product %s: %s", cj_id, e)
                    continue

        logger.info(
            "CJ sync complete for %s: added=%d, updated=%d",
            provider.name,
            products_added,
            products_updated,
        )

        return {
            "status": "success",
            "products_added": products_added,
            "products_updated": products_updated,
        }

    except Exception as exc:
        logger.exception("CJ sync failed: %s", exc)
        return {
            "status": "error",
            "products_added": 0,
            "products_updated": 0,
            "error": str(exc),
        }


def _sync_shareasale_products(provider, settings_obj) -> dict[str, Any]:
    """
    Sync products from ShareASale API v3.3.

    Uses the getDeals endpoint to fetch affiliate product offers,
    then creates/updates AffiliateProduct records.

    API Docs: https://docs.shareasale.com/api-docs
    Endpoint: https://api.shareasale.com/w.cfm
    """
    try:
        import defusedxml.ElementTree as ET
        import requests
    except ImportError:
        logger.warning(
            "requests or defusedxml library not available for ShareASale sync"
        )
        return {
            "status": "dependencies_not_available",
            "products_added": 0,
            "products_updated": 0,
        }

    if (
        not provider
        or not hasattr(provider, "api_key")
        or not hasattr(provider, "api_secret")
    ):
        logger.warning("ShareASale provider missing API credentials")
        return {
            "status": "missing_credentials",
            "products_added": 0,
            "products_updated": 0,
        }

    try:
        from apps.ads.models import AffiliateProduct

        api_key = provider.api_key
        api_secret = provider.api_secret

        products_added = 0
        products_updated = 0

        params = {
            "action": "getDeals",
            "token": api_key,
            "token2": api_secret,
            "output": "xml",
        }

        try:
            resp = requests.get(
                "https://api.shareasale.com/w.cfm", params=params, timeout=30
            )
            resp.raise_for_status()
            xml_data = resp.text
        except Exception as e:
            logger.error("ShareASale API request failed: %s", e)
            return {
                "status": "api_error",
                "products_added": 0,
                "products_updated": 0,
                "error": str(e),
            }

        try:
            root = ET.fromstring(xml_data)
        except ET.ParseError as e:
            logger.error("ShareASale XML parse error: %s", e)
            return {
                "status": "parse_error",
                "products_added": 0,
                "products_updated": 0,
            }

        # Parse deals from XML
        for deal in root.findall(".//deal"):
            try:
                deal_id = deal.findtext("dealid", "")
                name = deal.findtext("name", "")
                url = deal.findtext("dealurl", "")
                commission_str = deal.findtext("commission", "0")

                try:
                    commission_percent = (
                        Decimal(commission_str) if commission_str else Decimal("0")
                    )
                except Exception:
                    commission_percent = Decimal("0")

                if not name or not deal_id:
                    continue

                aff_prod, created = AffiliateProduct.objects.update_or_create(
                    provider=provider,
                    external_id=str(deal_id),
                    defaults={
                        "name": name,
                        "url": url,
                        "commission_rate": commission_percent,
                        "is_enabled": True,
                        "api_data": {
                            "shareasale_deal_id": deal_id,
                            "synced_at": timezone.now().isoformat(),
                        },
                    },
                )

                if created:
                    products_added += 1
                else:
                    products_updated += 1

            except Exception as e:
                logger.error("Error processing ShareASale deal %s: %s", deal_id, e)
                continue

        logger.info(
            "ShareASale sync complete for %s: added=%d, updated=%d",
            provider.name,
            products_added,
            products_updated,
        )

        return {
            "status": "success",
            "products_added": products_added,
            "products_updated": products_updated,
        }

    except Exception as exc:
        logger.exception("ShareASale sync failed: %s", exc)
        return {
            "status": "error",
            "products_added": 0,
            "products_updated": 0,
            "error": str(exc),
        }


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
    soft_time_limit=120,
    time_limit=180,
)
def track_affiliate_click(
    self,
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
    Track affiliate link click for analytics.
    Called via API endpoint when user clicks affiliate product link.
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
            referrer_type=referrer_type[:20] if referrer_type else "",
            referrer_id=referrer_id,
        )

        # Increment product click count
        product.clicks += 1
        product.save(update_fields=["clicks"])

        # Update provider stats
        product.provider.total_clicks += 1
        product.provider.save(update_fields=["total_clicks"])

        logger.info(f"Tracked click for product {product_id}")

        return {"status": "success", "click_id": click.pk}

    except Exception as exc:
        logger.exception(f"Failed to track affiliate click: {exc}")
        raise self.retry(exc=exc)  # noqa: B904


@shared_task(bind=True)
def update_affiliate_product_rankings(self) -> dict[str, Any]:
    """
    Update AI relevance scores for affiliate products.
    Based on click-through rates, conversion data, and targeting accuracy.
    """
    try:
        from apps.ads.models import AffiliateProduct

        products = AffiliateProduct.objects.filter(is_enabled=True)
        updated = 0

        for product in products:
            # Calculate relevance score based on performance
            base_score = 50.0

            # Boost for good click-through rate
            if product.impressions > 0:
                ctr = (product.clicks / product.impressions) * 100
                if ctr > 5:
                    base_score += 20
                elif ctr > 2:
                    base_score += 10
                elif ctr > 0.5:
                    base_score += 5

            # Boost for conversions
            if product.conversions > 0:
                conv_rate = (product.conversions / max(product.clicks, 1)) * 100
                if conv_rate > 5:
                    base_score += 25
                elif conv_rate > 2:
                    base_score += 15
                elif conv_rate > 0.5:
                    base_score += 8

            # Boost for good ratings
            if product.rating >= 4.5:
                base_score += 10
            elif product.rating >= 4.0:
                base_score += 5

            # Boost for review count
            if product.review_count > 1000:
                base_score += 10
            elif product.review_count > 100:
                base_score += 5

            # Penalty for out of stock
            if not product.is_in_stock:
                base_score -= 30

            # Cap score between 0-100
            product.ai_relevance_score = max(0, min(100, base_score))
            product.save(update_fields=["ai_relevance_score"])
            updated += 1

        logger.info(f"Updated relevance scores for {updated} products")
        return {"status": "success", "products_updated": updated}

    except Exception as exc:
        logger.exception(f"Failed to update product rankings: {exc}")
        return {"status": "error", "error": str(exc)}
