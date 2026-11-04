# Celery tasks for firmware analytics and tracking
"""
Periodic tasks to:
- Aggregate daily firmware statistics
- Update trending scores
- Clean old tracking data
"""

import logging
from datetime import timedelta

try:
    from celery import shared_task
except Exception:  # pragma: no cover - fallback when Celery not installed

    def shared_task(*dargs, **dkwargs):  # type: ignore[assignment]
        def decorator(func):
            return func

        if dargs and callable(dargs[0]):
            return dargs[0]
        return decorator


from django.apps import apps
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=120, time_limit=180)
def aggregate_daily_firmware_stats():
    """
    Daily task to aggregate FirmwareView and FirmwareDownloadAttempt into FirmwareStats
    Run at 1 AM daily via Celery Beat
    """
    if not apps.is_installed("apps.firmwares"):
        return {"error": "Firmwares app not installed"}

    try:
        FirmwareStats = apps.get_model("firmwares", "FirmwareStats")
        FirmwareView = apps.get_model("firmwares", "FirmwareView")
        FirmwareDownloadAttempt = apps.get_model("firmwares", "FirmwareDownloadAttempt")

        # Aggregate yesterday's data
        yesterday = (timezone.now() - timedelta(days=1)).date()

        # Get all unique firmwares that had activity yesterday
        from django.contrib.contenttypes.models import ContentType

        firmware_types = [
            "OfficialFirmware",
            "EngineeringFirmware",
            "ReadbackFirmware",
            "ModifiedFirmware",
            "OtherFirmware",
            "UnclassifiedFirmware",
        ]

        aggregated_count = 0

        for fw_type in firmware_types:
            try:
                Model = apps.get_model("firmwares", fw_type)
                content_type = ContentType.objects.get_for_model(Model)

                # Get unique firmware IDs that had activity
                firmware_ids = set()

                # From views
                views = (
                    FirmwareView.objects.filter(
                        content_type=content_type, viewed_at__date=yesterday
                    )
                    .values_list("object_id", flat=True)
                    .distinct()
                )
                firmware_ids.update(views)

                # From downloads
                downloads = (
                    FirmwareDownloadAttempt.objects.filter(
                        content_type=content_type, initiated_at__date=yesterday
                    )
                    .values_list("object_id", flat=True)
                    .distinct()
                )
                firmware_ids.update(downloads)

                # Aggregate stats for each firmware
                for fw_id in firmware_ids:
                    FirmwareStats.aggregate_for_date(
                        date=yesterday, firmware_ct=content_type, firmware_id=fw_id
                    )
                    aggregated_count += 1

            except Exception as e:
                logger.exception(f"Error aggregating {fw_type}: {e}")
                continue

        logger.info(f"Aggregated {aggregated_count} firmware stats for {yesterday}")
        return {"date": str(yesterday), "aggregated": aggregated_count}

    except Exception as e:
        logger.exception(f"Error in aggregate_daily_firmware_stats: {e}")
        return {"error": str(e)}


@shared_task(soft_time_limit=120, time_limit=180)
def cleanup_old_tracking_data(days_to_keep: int = 90):
    """
    Weekly task to clean up old tracking data (keep last 90 days)
    Helps prevent database bloat
    """
    if not apps.is_installed("apps.firmwares"):
        return {"error": "Firmwares app not installed"}

    try:
        FirmwareView = apps.get_model("firmwares", "FirmwareView")
        FirmwareDownloadAttempt = apps.get_model("firmwares", "FirmwareDownloadAttempt")

        cutoff_date = timezone.now() - timedelta(days=days_to_keep)

        # Delete old views
        views_deleted = FirmwareView.objects.filter(viewed_at__lt=cutoff_date).delete()[
            0
        ]

        # Delete old download attempts (except failed ones - keep for debugging)
        downloads_deleted = FirmwareDownloadAttempt.objects.filter(
            initiated_at__lt=cutoff_date, status__in=["completed", "cancelled"]
        ).delete()[0]

        logger.info(
            f"Cleaned up old tracking data: {views_deleted} views, "
            f"{downloads_deleted} download attempts older than {days_to_keep} days"
        )

        return {
            "views_deleted": views_deleted,
            "downloads_deleted": downloads_deleted,
            "cutoff_date": str(cutoff_date.date()),
        }

    except Exception as e:
        logger.exception(f"Error in cleanup_old_tracking_data: {e}")
        return {"error": str(e)}


@shared_task(soft_time_limit=60, time_limit=120)
def update_firmware_request_priorities():
    """
    Weekly task to recalculate firmware request priorities based on:
    - Request count
    - Urgency
    - Time since last request
    - Number of unique requesters
    """
    if not apps.is_installed("apps.firmwares"):
        return {"error": "Firmwares app not installed"}

    try:
        FirmwareRequest = apps.get_model("firmwares", "FirmwareRequest")

        open_requests = FirmwareRequest.objects.filter(status="open")

        # Could add a priority_score field to model and update it here
        # For now, just log the current state

        by_count = open_requests.order_by("-request_count")[:10]

        top_requests = []
        for req in by_count:
            top_requests.append(
                {
                    "brand": req.brand.name,
                    "model": req.model.name if req.model else "N/A",
                    "count": req.request_count,
                    "urgency": req.urgency,
                }
            )

        logger.info(f"Top 10 requested firmwares: {top_requests}")

        return {
            "total_open": open_requests.count(),
            "top_requests": top_requests,
        }

    except Exception as e:
        logger.exception(f"Error in update_firmware_request_priorities: {e}")
        return {"error": str(e)}


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=300,
    time_limit=600,
)
def analyze_firmware_ai(self, pending_firmware_id: int):
    """
    AI-powered firmware analysis task.
    Extracts metadata from firmware binary using pattern matching and AI.

    Args:
        pending_firmware_id: ID of PendingFirmware instance to analyze

    Returns:
        Analysis result dictionary
    """
    try:
        from apps.firmwares.ai_analysis import analyze_and_update_firmware

        result = analyze_and_update_firmware(pending_firmware_id)

        if result.get("success"):
            logger.info(f"AI firmware analysis completed for {pending_firmware_id}")
            return result
        else:
            logger.warning(
                f"AI firmware analysis failed for {pending_firmware_id}: {result.get('error')}"
            )
            return result

    except Exception as exc:
        logger.error(f"AI firmware analysis task failed: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=60)  # noqa: B904


@shared_task(soft_time_limit=30, time_limit=60)
def log_firmware_view(
    firmware_ct_id: int,
    firmware_id: str,
    user_id: int | None = None,
    session_key: str | None = None,
    ip_address: str | None = None,
):
    """
    Async task to log firmware view (called from views to avoid blocking response)

    Args:
        firmware_ct_id: ContentType ID of firmware
        firmware_id: UUID of firmware
        user_id: User ID if authenticated
        session_key: Session key for anonymous
        ip_address: IP address
    """
    try:
        FirmwareView = apps.get_model("firmwares", "FirmwareView")
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get(id=firmware_ct_id)

        user = None
        if user_id:
            User = apps.get_model("users", "CustomUser")
            try:
                user = User.objects.get(id=user_id)
            except Exception:  # noqa: S110
                pass

        FirmwareView.objects.create(
            content_type=content_type,
            object_id=firmware_id,
            user=user,
            session_key=session_key or "",
            ip_address=ip_address,
        )

    except Exception as e:
        logger.warning(f"Failed to log firmware view: {e}")
        # Don't fail the main request if logging fails


# ============================================================
# Blog Generation Tasks
# ============================================================


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    soft_time_limit=300,
    time_limit=600,
)
def generate_firmware_blog_post(self, model_id: int, force_update: bool = False):
    """
    Async task to generate a governed blog post for a specific device model.
    Called when new firmware is uploaded via signal handler.

    Args:
        model_id: The ID of the Model (device) to generate blog for
        force_update: If True, create a post even when the model has no listings.
    """
    try:
        Model = apps.get_model("firmwares", "Model")
        model = Model.objects.select_related("brand").get(id=model_id)

        from apps.firmwares.blog_automation import FirmwareBlogService

        post = FirmwareBlogService.generate_firmware_post(
            model, force_update=force_update
        )

        if post:
            logger.info(
                f"Blog post generated for {model.brand.name} {model.name}: {post.slug}"
            )
            return {
                "status": "success",
                "model": f"{model.brand.name} {model.name}",
                "post_id": post.id,
                "post_slug": post.slug,
            }
        else:
            logger.info(f"Blog post skipped for {model.brand.name} {model.name}")
            return {
                "status": "skipped",
                "model": f"{model.brand.name} {model.name}",
            }

    except Model.DoesNotExist:
        logger.exception(f"Model with id {model_id} not found")
        return {"status": "error", "error": f"Model {model_id} not found"}

    except Exception as e:
        logger.exception(f"Error generating blog for model {model_id}: {e}")
        # Retry on failure
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.error(
                "Firmware blog generation exhausted retries for model %s",
                model_id,
            )
            raise


@shared_task(soft_time_limit=3600, time_limit=7200)
def generate_all_firmware_blogs(
    force_update: bool = False, brand_name: str | None = None
):
    """
    Bulk task to generate blog posts for all models with firmware.
    Can be triggered via management command with --async flag.

    Args:
        force_update: If True, regenerate all posts even if they exist
        brand_name: Optional - only process models from this brand
    """
    try:
        Model = apps.get_model("firmwares", "Model")
        OfficialFirmware = apps.get_model("firmwares", "OfficialFirmware")
        EngineeringFirmware = apps.get_model("firmwares", "EngineeringFirmware")
        ReadbackFirmware = apps.get_model("firmwares", "ReadbackFirmware")
        ModifiedFirmware = apps.get_model("firmwares", "ModifiedFirmware")
        OtherFirmware = apps.get_model("firmwares", "OtherFirmware")

        from apps.firmwares.blog_automation import FirmwareBlogService

        # Get models queryset
        models_qs = Model.objects.select_related("brand")
        if brand_name:
            models_qs = models_qs.filter(brand__name__iexact=brand_name)

        success_count = 0
        skip_count = 0
        error_count = 0

        for model in models_qs:
            try:
                # Check if model has any active firmware
                has_firmware = (
                    OfficialFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or EngineeringFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or ReadbackFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or ModifiedFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                    or OtherFirmware.objects.filter(
                        model=model, is_active=True
                    ).exists()
                )

                if not has_firmware and not force_update:
                    skip_count += 1
                    continue

                post = FirmwareBlogService.generate_firmware_post(
                    model, force_update=force_update
                )

                if post:
                    success_count += 1
                else:
                    skip_count += 1

            except Exception as e:
                logger.exception(f"Error generating blog for {model}: {e}")
                error_count += 1

        result = {
            "status": "completed",
            "success_count": success_count,
            "skip_count": skip_count,
            "error_count": error_count,
        }

        logger.info(f"Bulk blog generation completed: {result}")
        return result

    except Exception as e:
        logger.exception(f"Error in generate_all_firmware_blogs: {e}")
        return {"status": "error", "error": str(e)}


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=600,
    time_limit=900,
)
def catchup_missing_blog_posts(self):
    """
    Periodic catch-up task: finds firmware Models that have no blog post
    and generates one. Runs on a schedule to recover from signal failures,
    Celery outages, or any other missed blog generation.
    """
    try:
        Model = apps.get_model("firmwares", "Model")
        Post = apps.get_model("blog", "Post")
        from apps.firmwares.blog_automation import FirmwareBlogService

        # Find models with no blog post (firmware_model FK is null for them)
        models_with_posts = Post.objects.filter(
            firmware_model__isnull=False,
        ).values_list("firmware_model_id", flat=True)

        missing_models = Model.objects.select_related("brand").exclude(
            id__in=models_with_posts
        )

        created_count = 0
        error_count = 0

        for model in missing_models:
            try:
                post = FirmwareBlogService.generate_firmware_post(
                    model, force_update=True
                )
                if post:
                    logger.info(
                        f"Catch-up: created blog post for {model.brand.name} {model.name}"
                    )
                    created_count += 1
            except Exception as e:
                logger.warning(f"Catch-up: failed to create blog post for {model}: {e}")
                error_count += 1

        result = {
            "status": "completed",
            "missing_found": missing_models.count() + created_count,
            "created": created_count,
            "errors": error_count,
        }
        if created_count:
            logger.info(f"Blog catch-up completed: {result}")
        return result

    except Exception as e:
        logger.exception(f"Error in catchup_missing_blog_posts: {e}")
        try:
            raise self.retry(exc=e)
        except self.MaxRetriesExceededError:
            return {"status": "error", "error": str(e)}
