# Signal handlers for firmware tracking and connected-app integrations
"""
Handles:
- Firmware upload tracking
- Download tracking and completion
- Analytics updates

All handlers use async tasks where possible to avoid blocking requests
"""

import logging

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _schedule_firmware_blog_post(model_id: int, *, force_update: bool = False) -> None:
    def generate() -> None:
        try:
            if getattr(settings, "FIRMWARE_BLOG_AUTOGENERATION_ASYNC", False):
                from .tasks import generate_firmware_blog_post

                try:
                    generate_firmware_blog_post.delay(  # type: ignore[attr-defined]
                        model_id=model_id,
                        force_update=force_update,
                    )
                    logger.info(
                        "Queued firmware blog generation for model %s", model_id
                    )
                    return
                except Exception:
                    logger.exception(
                        "Could not queue firmware blog generation for model %s; "
                        "falling back to synchronous generation",
                        model_id,
                    )

            model = (
                apps.get_model("firmwares", "Model")
                .objects.select_related("brand")
                .get(pk=model_id)
            )
            from .blog_automation import FirmwareBlogService

            FirmwareBlogService.generate_firmware_post(
                model,
                force_update=force_update,
            )
        except Exception:
            logger.exception(
                "Firmware blog generation failed for model %s",
                model_id,
            )

    transaction.on_commit(generate)


# Import autofill system
try:
    from apps.firmwares.admin_autofill import FirmwareAutoFill
except ImportError:
    FirmwareAutoFill = None


# === FIRMWARE MODEL SIGNALS (For Multi-Brand Schema) ===


@receiver(post_save, sender="firmwares.Brand")
def invalidate_brand_caches(sender, instance, created, **kwargs):
    """When brand created/updated, invalidate relevant caches and create blog category"""
    if created:
        # Auto-create blog category for brand
        from .blog_automation import FirmwareBlogService

        try:
            FirmwareBlogService.ensure_brand_category(instance)
            logger.info(f"Blog category created for brand: {instance.name}")
        except Exception as e:
            logger.warning(f"Failed to create blog category: {e}")


@receiver(post_save, sender="firmwares.Model")
def handle_model_created(sender, instance, created, **kwargs):
    """When model created, create blog category and generate blog post"""
    if created:
        from .blog_automation import FirmwareBlogService

        try:
            FirmwareBlogService.ensure_model_category(instance)
            logger.info(f"Blog category created for model: {instance.name}")
        except Exception as e:
            logger.warning(f"Failed to create blog category: {e}")

        # Auto-generate blog post for the new model
        _schedule_firmware_blog_post(instance.pk, force_update=True)


@receiver(post_save, sender="firmwares.OfficialFirmware")
@receiver(post_save, sender="firmwares.EngineeringFirmware")
@receiver(post_save, sender="firmwares.ReadbackFirmware")
@receiver(post_save, sender="firmwares.ModifiedFirmware")
@receiver(post_save, sender="firmwares.OtherFirmware")
@receiver(post_save, sender="firmwares.UnclassifiedFirmware")
def handle_firmware_uploaded_blog(sender, instance, created, **kwargs):
    """
    When firmware uploaded, auto-generate/update blog post for that model
    Lists all available firmwares across all variants

    Uses Celery for async processing when available, falls back to sync.
    """
    if created and instance.model:
        _schedule_firmware_blog_post(instance.model_id)


@receiver(post_delete, sender="firmwares.OfficialFirmware")
@receiver(post_delete, sender="firmwares.EngineeringFirmware")
@receiver(post_delete, sender="firmwares.ReadbackFirmware")
@receiver(post_delete, sender="firmwares.ModifiedFirmware")
@receiver(post_delete, sender="firmwares.OtherFirmware")
@receiver(post_delete, sender="firmwares.UnclassifiedFirmware")
def handle_firmware_deleted_blog(sender, instance, **kwargs):
    """When firmware deleted, update blog post to reflect changes"""
    if instance.model:
        from .blog_automation import FirmwareBlogService

        try:
            # Regenerate blog post with updated firmware list
            post = FirmwareBlogService.generate_firmware_post(
                instance.model, force_update=True
            )
            if post:
                logger.info(f"Blog post updated after firmware deletion: {post.title}")
        except Exception as e:
            logger.exception(f"Failed to update blog post: {e}")


@receiver(post_save, sender="firmwares.PendingFirmware")
def handle_pending_firmware_decision(sender, instance, **kwargs):
    """
    When pending firmware approved/rejected:
    1. If approved → convert to appropriate firmware type
    2. If rejected → cleanup file
    3. Notify uploader
    """
    if apps.is_installed("apps.governance"):
        from django.contrib.contenttypes.models import ContentType

        from apps.governance.models import ReviewCase
        from apps.governance.services import submit_for_review

        content_type = ContentType.objects.get_for_model(
            instance, for_concrete_model=False
        )
        has_case = ReviewCase.objects.filter(
            content_type=content_type,
            object_id=str(instance.pk),
        ).exists()
        if has_case:
            return
        if instance.admin_decision == "pending":
            submit_for_review(instance, actor=instance.uploader)
            return
        raise PermissionError(
            "Firmware decisions require an existing governance review case."
        )

    if instance.admin_decision == "approved" and not kwargs.get("created"):
        # Check if already processed
        if hasattr(instance, "_processed"):
            return

        try:
            # Convert to appropriate firmware type based on ai_category
            category_map = {
                "official": "OfficialFirmware",
                "engineering": "EngineeringFirmware",
                "readback": "ReadbackFirmware",
                "modified": "ModifiedFirmware",
                "other": "OtherFirmware",
                "unclassified": "UnclassifiedFirmware",
            }

            target_model_name = category_map.get(instance.ai_category, "OtherFirmware")
            TargetModel = apps.get_model("firmwares", target_model_name)

            # Create new firmware
            firmware = TargetModel.objects.create(
                original_file_name=instance.original_file_name,
                stored_file_path=instance.stored_file_path,
                uploader=instance.uploader,
                brand=instance.uploaded_brand,
                model=instance.uploaded_model,
                variant=instance.uploaded_variant,
                chipset=instance.chipset,
                package_format=instance.package_format,
                partitions=instance.partitions,
                is_password_protected=instance.is_password_protected,
                encrypted_password=instance.encrypted_password,
                metadata=instance.metadata,
            )

            # Mark as processed
            instance._processed = True

            logger.info(
                f"Converted pending firmware {instance.id} to {target_model_name}: {firmware.id}"
            )

            # Auto-generate blog post for this model if it has firmwares now
            if firmware.model:
                from .blog_automation import FirmwareBlogService

                try:
                    post = FirmwareBlogService.generate_firmware_post(firmware.model)
                    if post:
                        logger.info(
                            f"Blog post created for approved firmware: {post.title}"
                        )
                except Exception as e:
                    logger.exception(
                        f"Failed to generate blog post for approved firmware: {e}"
                    )

            # Notify uploader of approval
            if instance.uploader:
                try:
                    from apps.core.signals import notification_send_requested

                    notification_send_requested.send(
                        sender=sender,
                        user_ids=[instance.uploader_id],
                        action_type="moderation",
                        priority="info",
                        title="Firmware Approved",
                        message=f'Your firmware upload "{instance.original_file_name}" has been approved.',
                    )
                except Exception as e:
                    logger.warning(f"Failed to send approval notification: {e}")

        except Exception as e:
            logger.exception(f"Error converting pending firmware: {e}")

    elif instance.admin_decision == "rejected":
        # Cleanup file and notify uploader
        logger.info(f"Pending firmware rejected: {instance.id}")

        # Delete the stored file
        if instance.stored_file_path:
            try:
                import os

                if os.path.exists(instance.stored_file_path):
                    os.remove(instance.stored_file_path)
                    logger.info(
                        f"Deleted rejected firmware file: {instance.stored_file_path}"
                    )
            except Exception as e:
                logger.exception(f"Failed to delete rejected firmware file: {e}")

        # Notify uploader of rejection
        if instance.uploader:
            try:
                from apps.core.signals import notification_send_requested

                notification_send_requested.send(
                    sender=sender,
                    user_ids=[instance.uploader_id],
                    action_type="moderation",
                    priority="warning",
                    title="Firmware Rejected",
                    message=f'Your firmware upload "{instance.original_file_name}" was rejected. Reason: {instance.admin_notes or "Not specified"}',
                )
            except Exception as e:
                logger.warning(f"Failed to send rejection notification: {e}")


# === FIRMWARE REQUEST SIGNALS ===


@receiver(post_save, sender="firmwares.FirmwareRequest")
def handle_firmware_request(sender, instance, created, **kwargs):
    """
    When firmware requested:
    1. Invalidate most_requested cache
    2. Notify admins if high urgency
    3. Check if similar firmware already exists
    """
    if created:
        # If urgent, notify admins
        if instance.urgency >= 3:  # High or Urgent
            logger.warning(
                f"Urgent firmware request: {instance.brand}/{instance.model} "
                f"({instance.request_count} requests)"
            )


# === VIEW/DOWNLOAD TRACKING (For Performance) ===
# Note: These are handled via async tasks (tasks.py) to avoid blocking HTTP responses
# Signals here would be too slow for high-traffic scenarios


# === AUTO-FILL SIGNALS ===
# Automatically fill missing firmware metadata from AI and internet sources

if FirmwareAutoFill:
    from apps.firmwares.models import Brand, Model, Variant

    @receiver(post_save, sender=Brand)
    def autofill_brand_on_create(sender, instance, created, **kwargs):
        """Auto-fill brand fields on creation.

        Note: Brand model only has name/slug/logo_url — no description field.
        Auto-fill logo_url if an external source provides one.
        """
        if not created:
            return

        try:
            if not instance.logo_url:
                data = FirmwareAutoFill.autofill_brand(  # type: ignore[union-attr]
                    instance.name
                )
                if data.get("logo_url"):
                    instance.logo_url = data["logo_url"]
                    instance.save(update_fields=["logo_url"])
                    logger.info(f"Auto-filled brand logo: {instance.name}")
        except Exception as e:
            logger.exception(f"Failed to autofill brand {instance.name}: {e}")

    @receiver(post_save, sender=Model)
    def autofill_model_on_create(sender, instance, created, **kwargs):
        """Auto-fill model fields on creation"""
        if not created:
            return

        try:
            needs_update = False
            update_fields = []

            if not instance.marketing_name:
                data = FirmwareAutoFill.autofill_model(  # type: ignore[union-attr]
                    instance.brand.name, instance.name
                )
                if data.get("marketing_name"):
                    instance.marketing_name = data["marketing_name"]
                    update_fields.append("marketing_name")
                    needs_update = True

            if not instance.model_code:
                if "data" not in locals():
                    data = FirmwareAutoFill.autofill_model(  # type: ignore[union-attr]
                        instance.brand.name, instance.name
                    )
                if data.get("model_code"):
                    instance.model_code = data["model_code"]
                    update_fields.append("model_code")
                    needs_update = True

            if not instance.description:
                if "data" not in locals():
                    data = FirmwareAutoFill.autofill_model(  # type: ignore[union-attr]
                        instance.brand.name, instance.name
                    )
                if data.get("description"):
                    instance.description = data["description"]
                    update_fields.append("description")
                    needs_update = True

            if needs_update:
                instance.save(update_fields=update_fields)
                logger.info(f"Auto-filled model: {instance.brand.name} {instance.name}")
        except Exception as e:
            logger.exception(f"Failed to autofill model {instance.name}: {e}")

    @receiver(post_save, sender=Variant)
    def autofill_variant_on_create(sender, instance, created, **kwargs):
        """Auto-fill variant fields on creation"""
        if not created:
            return

        try:
            needs_update = False
            update_fields = []

            if not instance.chipset:
                data = FirmwareAutoFill.autofill_variant(  # type: ignore[union-attr]
                    instance.model.brand.name, instance.model.name, instance.region
                )
                if data.get("chipset"):
                    instance.chipset = data["chipset"]
                    update_fields.append("chipset")
                    needs_update = True

            if not instance.ram_options:
                if "data" not in locals():
                    data = FirmwareAutoFill.autofill_variant(  # type: ignore[union-attr]
                        instance.model.brand.name, instance.model.name, instance.region
                    )
                if data.get("ram_options"):
                    instance.ram_options = data["ram_options"]
                    update_fields.append("ram_options")
                    needs_update = True

            if not instance.storage_options:
                if "data" not in locals():
                    data = FirmwareAutoFill.autofill_variant(  # type: ignore[union-attr]
                        instance.model.brand.name, instance.model.name, instance.region
                    )
                if data.get("storage_options"):
                    instance.storage_options = data["storage_options"]
                    update_fields.append("storage_options")
                    needs_update = True

            if needs_update:
                instance.save(update_fields=update_fields)
                logger.info(
                    f"Auto-filled variant: {instance.model.brand.name} {instance.model.name} {instance.name}"
                )
        except Exception as e:
            logger.exception(f"Failed to autofill variant {instance.name}: {e}")
