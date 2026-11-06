"""Celery tasks for firmware analytics, token hygiene and automated blog sync."""

import logging
from datetime import timedelta

from celery import shared_task
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.utils import timezone

from .catalog import PUBLIC_FIRMWARE_TYPES
from .download_service import expire_stale_tokens
from .tracking_models import (
    FirmwareDownloadAttempt,
    FirmwareRequest,
    FirmwareStats,
    FirmwareView,
)

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=120, time_limit=180)
def aggregate_daily_firmware_stats():
    """Aggregate yesterday's FirmwareView and FirmwareDownloadAttempt rows."""
    yesterday = (timezone.now() - timedelta(days=1)).date()
    aggregated = 0
    for _key, model_class in PUBLIC_FIRMWARE_TYPES:
        content_type = ContentType.objects.get_for_model(
            model_class, for_concrete_model=False
        )
        firmware_ids = set(
            FirmwareView.objects.filter(
                content_type=content_type, viewed_at__date=yesterday
            ).values_list("object_id", flat=True)
        ) | set(
            FirmwareDownloadAttempt.objects.filter(
                content_type=content_type, initiated_at__date=yesterday
            ).values_list("object_id", flat=True)
        )
        for firmware_id in firmware_ids:
            FirmwareStats.aggregate_for_date(
                date=yesterday, firmware_ct=content_type, firmware_id=firmware_id
            )
            aggregated += 1
    logger.info("Aggregated %s firmware stats for %s", aggregated, yesterday)
    return {"date": str(yesterday), "aggregated": aggregated}


@shared_task(soft_time_limit=120, time_limit=180)
def cleanup_old_tracking_data(days_to_keep: int = 90):
    """Delete view logs and settled download attempts older than the retention window."""
    cutoff = timezone.now() - timedelta(days=days_to_keep)
    views_deleted = FirmwareView.objects.filter(viewed_at__lt=cutoff).delete()[0]
    downloads_deleted = FirmwareDownloadAttempt.objects.filter(
        initiated_at__lt=cutoff,
        status__in=["ready", "downloading", "completed", "cancelled"],
    ).delete()[0]
    return {
        "views_deleted": views_deleted,
        "downloads_deleted": downloads_deleted,
        "cutoff_date": str(cutoff.date()),
    }


@shared_task(soft_time_limit=60, time_limit=120)
def expire_download_tokens():
    """Mark download tokens past their expiry as expired."""
    expired = expire_stale_tokens()
    return {"expired": expired}


@shared_task(soft_time_limit=60, time_limit=120)
def update_firmware_request_priorities():
    """Report the most requested open firmware (the queue is ordered by demand)."""
    open_requests = FirmwareRequest.objects.filter(status="open")
    top = [
        {
            "brand": request.brand.name,
            "model": request.model.name if request.model else "N/A",
            "count": request.request_count,
            "urgency": request.urgency,
        }
        for request in open_requests.select_related("brand", "model").order_by(
            "-request_count"
        )[:10]
    ]
    logger.info("Top requested firmware: %s", top)
    return {"total_open": open_requests.count(), "top_requests": top}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=600,
)
def analyze_firmware_ai(self, pending_firmware_id: str):
    """Analyse a pending upload's content and store metadata suggestions."""
    from .ai_analysis import analyze_and_update_firmware

    result = analyze_and_update_firmware(pending_firmware_id)
    if not result.get("success"):
        logger.warning(
            "Firmware analysis failed for %s: %s",
            pending_firmware_id,
            result.get("error"),
        )
    return result


# ============================================================
# Automated blog tasks
# ============================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=600,
    time_limit=900,
)
def sync_automated_blogs(self, model_ids=(), brand_ids=(), chipset_ids=()):
    """Refresh the automated brand, model and chipset posts for the given ids."""
    from .blog_automation import FirmwareBlogService
    from .models import Brand, Chipset, Model

    try:
        synced = 0
        for brand in Brand.objects.filter(pk__in=brand_ids):
            FirmwareBlogService.sync_brand_post(brand)
            synced += 1
        for model in Model.objects.filter(pk__in=model_ids):
            FirmwareBlogService.sync_model_post(model)
            synced += 1
        for chipset in Chipset.objects.filter(pk__in=chipset_ids):
            FirmwareBlogService.sync_chipset_post(chipset)
            synced += 1
    except DatabaseError as exc:
        raise self.retry(exc=exc) from exc
    return {"synced": synced}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=300,
    time_limit=600,
)
def generate_firmware_blog_post(self, model_id: int, force_update: bool = False):
    """Sync the single automated post for one device model."""
    from .blog_automation import FirmwareBlogService
    from .models import Model

    model = Model.objects.select_related("brand").filter(pk=model_id).first()
    if model is None:
        return {"status": "error", "error": f"Model {model_id} not found"}
    try:
        post = FirmwareBlogService.sync_model_post(model)
    except DatabaseError as exc:
        raise self.retry(exc=exc) from exc
    return {
        "status": "success" if post else "skipped",
        "model": f"{model.brand.name} {model.name}",
        "post_id": post.pk if post else None,
    }


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=300,
    time_limit=600,
)
def generate_firmware_brand_blog_post(self, brand_id: int):
    """Sync the single automated post for one brand."""
    from .blog_automation import FirmwareBlogService
    from .models import Brand

    brand = Brand.objects.filter(pk=brand_id).first()
    if brand is None:
        return {"status": "error", "error": f"Brand {brand_id} not found"}
    try:
        post = FirmwareBlogService.sync_brand_post(brand)
    except DatabaseError as exc:
        raise self.retry(exc=exc) from exc
    return {
        "status": "success" if post else "skipped",
        "brand": brand.slug,
        "post_id": post.pk if post else None,
    }


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=1800,
    time_limit=3600,
)
def catchup_missing_blog_posts(self, brand_slug: str | None = None):
    """Periodic catch-up: every brand, model and chipset gets a current post.

    Recovers from missed signals or queue outages; unchanged posts are skipped via
    their content hash so repeated runs are cheap.
    """
    from .blog_automation import FirmwareBlogService

    result = FirmwareBlogService.sync_all(brand_slug=brand_slug)
    if result["errors"]:
        logger.warning("Blog catch-up finished with errors: %s", result)
    return {"status": "completed", **result}
