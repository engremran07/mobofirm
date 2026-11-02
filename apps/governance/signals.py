import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

from .services import submit_for_review

logger = logging.getLogger(__name__)


@receiver(post_save, sender="firmwares.PendingFirmware")
def govern_pending_firmware(sender, instance, created, **kwargs):
    if created:
        submit_for_review(instance, actor=instance.uploader)
        logger.info("Submitted firmware upload %s for governed review", instance.pk)


@receiver(post_save, sender="tags.Tag")
def govern_ai_suggested_tag(sender, instance, created, **kwargs):
    if created and instance.ai_suggested and not instance.is_curated:
        submit_for_review(instance)
        logger.info("Submitted AI-suggested tag %s for governed review", instance.pk)
