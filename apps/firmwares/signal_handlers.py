"""Signal handlers connecting firmware records to catalogue automation.

- Firmware, model, variant, brand and chipset changes refresh the automated blog
  posts (one per brand, model and chipset) after the transaction commits.
"""

import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

FIRMWARE_SENDERS = (
    "firmwares.OfficialFirmware",
    "firmwares.EngineeringFirmware",
    "firmwares.ReadbackFirmware",
    "firmwares.ModifiedFirmware",
    "firmwares.OtherFirmware",
    "firmwares.UnclassifiedFirmware",
)

_BLOG_REFRESH_FIELDS = {
    "brand",
    "brand_id",
    "model",
    "model_id",
    "variant",
    "variant_id",
    "is_active",
    "is_verified",
    "original_file_name",
    "stored_file_path",
    "external_url",
    "source_type",
    "build_number",
    "android_version",
    "package_format",
    "file_size",
}


def _run_blog_sync(model_ids: set, brand_ids: set, chipset_ids: set) -> None:
    from .blog_automation import FirmwareBlogService
    from .models import Brand, Chipset, Model

    try:
        for brand in Brand.objects.filter(pk__in=brand_ids):
            FirmwareBlogService.sync_brand_post(brand)
        for model in Model.objects.filter(pk__in=model_ids):
            FirmwareBlogService.sync_model_post(model)
        for chipset in Chipset.objects.filter(pk__in=chipset_ids):
            FirmwareBlogService.sync_chipset_post(chipset)
    except (DatabaseError, ValidationError, ValueError):
        logger.exception(
            "Automated blog sync failed (models=%s brands=%s chipsets=%s)",
            sorted(model_ids),
            sorted(brand_ids),
            sorted(chipset_ids),
        )


def schedule_blog_sync(*, model_ids=(), brand_ids=(), chipset_ids=()) -> None:
    """Queue a refresh of the automated posts once the current transaction commits."""
    models_, brands, chipsets = set(model_ids), set(brand_ids), set(chipset_ids)
    if not (models_ or brands or chipsets):
        return

    def dispatch() -> None:
        if getattr(settings, "FIRMWARE_BLOG_AUTOGENERATION_ASYNC", False):
            from kombu.exceptions import OperationalError

            from .tasks import sync_automated_blogs

            try:
                sync_automated_blogs.delay(
                    model_ids=sorted(models_),
                    brand_ids=sorted(brands),
                    chipset_ids=sorted(chipsets),
                )
            except (OperationalError, OSError):
                logger.exception(
                    "Could not queue automated blog sync; running it synchronously"
                )
            else:
                return
        _run_blog_sync(models_, brands, chipsets)

    transaction.on_commit(dispatch)


def _firmware_targets(firmware, *extra_model_ids) -> tuple[set, set, set]:
    """Model, brand and chipset ids whose automated posts list this firmware."""
    from .models import Model, Variant

    model_ids = {firmware.model_id, *extra_model_ids} - {None}
    brand_ids = {firmware.brand_id} - {None}
    chipset_ids: set = set()
    if firmware.model_id:
        row = (
            Model.objects.filter(pk=firmware.model_id)
            .values("brand_id", "chipset_ref_id")
            .first()
        )
        if row:
            brand_ids.add(row["brand_id"])
            chipset_ids.add(row["chipset_ref_id"])
    if firmware.variant_id:
        chipset_ids.add(
            Variant.objects.filter(pk=firmware.variant_id)
            .values_list("chipset_ref_id", flat=True)
            .first()
        )
    return model_ids, brand_ids, chipset_ids - {None}


@receiver(pre_save, sender=FIRMWARE_SENDERS[0])
@receiver(pre_save, sender=FIRMWARE_SENDERS[1])
@receiver(pre_save, sender=FIRMWARE_SENDERS[2])
@receiver(pre_save, sender=FIRMWARE_SENDERS[3])
@receiver(pre_save, sender=FIRMWARE_SENDERS[4])
@receiver(pre_save, sender=FIRMWARE_SENDERS[5])
def remember_firmware_blog_model(sender, instance, update_fields=None, **kwargs):
    """Remember the old model so reassigning a firmware refreshes both posts."""
    instance.blog_previous_model_id = None
    if not instance.pk or (
        update_fields is not None
        and not {"model", "model_id"}.intersection(update_fields)
    ):
        return
    instance.blog_previous_model_id = (
        sender.objects.filter(pk=instance.pk).values_list("model_id", flat=True).first()
    )


@receiver(post_save, sender=FIRMWARE_SENDERS[0])
@receiver(post_save, sender=FIRMWARE_SENDERS[1])
@receiver(post_save, sender=FIRMWARE_SENDERS[2])
@receiver(post_save, sender=FIRMWARE_SENDERS[3])
@receiver(post_save, sender=FIRMWARE_SENDERS[4])
@receiver(post_save, sender=FIRMWARE_SENDERS[5])
def handle_firmware_saved(instance, created, update_fields=None, **kwargs):
    """Refresh the posts a firmware feeds when it is created or materially changed."""
    if (
        not created
        and update_fields is not None
        and not _BLOG_REFRESH_FIELDS.intersection(update_fields)
    ):
        return
    previous = getattr(instance, "blog_previous_model_id", None)
    model_ids, brand_ids, chipset_ids = _firmware_targets(instance, previous)
    schedule_blog_sync(
        model_ids=model_ids, brand_ids=brand_ids, chipset_ids=chipset_ids
    )


@receiver(post_delete, sender=FIRMWARE_SENDERS[0])
@receiver(post_delete, sender=FIRMWARE_SENDERS[1])
@receiver(post_delete, sender=FIRMWARE_SENDERS[2])
@receiver(post_delete, sender=FIRMWARE_SENDERS[3])
@receiver(post_delete, sender=FIRMWARE_SENDERS[4])
@receiver(post_delete, sender=FIRMWARE_SENDERS[5])
def handle_firmware_deleted(instance, **kwargs):
    """Drop deleted firmware from its posts and invalidate its download tokens."""
    from .models import DownloadToken

    DownloadToken.objects.for_firmware(instance).delete()
    model_ids, brand_ids, chipset_ids = _firmware_targets(instance)
    schedule_blog_sync(
        model_ids=model_ids, brand_ids=brand_ids, chipset_ids=chipset_ids
    )


@receiver(post_save, sender="firmwares.Brand")
def handle_brand_saved(instance, created, **kwargs):
    if created:
        schedule_blog_sync(brand_ids={instance.pk})


@receiver(post_save, sender="firmwares.Model")
def handle_model_saved(instance, created, **kwargs):
    """Every model owns one post; saving the model refreshes it and its brand post."""
    chipsets = {instance.chipset_ref_id} - {None}
    schedule_blog_sync(
        model_ids={instance.pk},
        brand_ids={instance.brand_id} if created else (),
        chipset_ids=chipsets,
    )


@receiver(post_save, sender="firmwares.Variant")
def handle_variant_saved(instance, **kwargs):
    chipsets = {instance.chipset_ref_id} - {None}
    schedule_blog_sync(model_ids={instance.model_id}, chipset_ids=chipsets)


@receiver(post_save, sender="firmwares.Chipset")
def handle_chipset_saved(instance, **kwargs):
    schedule_blog_sync(chipset_ids={instance.pk})


@receiver(post_save, sender="firmwares.FirmwareRequest")
def handle_firmware_request(instance, created, **kwargs):
    """Log urgent firmware requests so staff can prioritise them."""
    if created and instance.urgency >= 3:
        logger.warning(
            "Urgent firmware request: %s/%s (%s requests)",
            instance.brand,
            instance.model,
            instance.request_count,
        )
