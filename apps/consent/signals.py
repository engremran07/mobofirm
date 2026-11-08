from __future__ import annotations

from typing import Any

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.consent.models import ConsentCategory, ConsentPolicy
from apps.consent.services import ensure_defaults, invalidate_policy_cache


def ensure_defaults_after_migrate(**kwargs: Any) -> None:
    ensure_defaults()


@receiver(post_save, sender=ConsentPolicy, dispatch_uid="consent.policy_saved")
@receiver(post_delete, sender=ConsentPolicy, dispatch_uid="consent.policy_deleted")
@receiver(post_save, sender=ConsentCategory, dispatch_uid="consent.category_saved")
@receiver(post_delete, sender=ConsentCategory, dispatch_uid="consent.category_deleted")
def drop_cached_policy(**kwargs: Any) -> None:
    invalidate_policy_cache()
