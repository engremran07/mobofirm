"""Cache invalidation for the site settings snapshot."""

from __future__ import annotations

import logging
from typing import Any

from django.core.signals import setting_changed
from django.db import transaction
from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import SiteSettings, VerificationFile, VerificationMetaTag
from .services import clear_site_settings_cache

logger = logging.getLogger(__name__)

# Django settings that feed the snapshot as defaults or hard floors.
WATCHED_SETTINGS = frozenset(
    {
        "SITE_NAME",
        "SITE_DESCRIPTION",
        "SITE_URL",
        "SITE_CONTACT_EMAIL",
        "SITE_LOGO_URL",
        "SITE_DARK_LOGO_URL",
        "SITE_FAVICON_URL",
        "LANGUAGE_CODE",
        "TIME_ZONE",
        "MAINTENANCE_MODE",
        "FORCE_HTTPS",
        "DEFAULT_FROM_EMAIL",
        "REQUIRE_PROFILE_COMPLETION",
        "RECAPTCHA_ENABLED",
        "RECAPTCHA_MODE",
        "RECAPTCHA_SITE_KEY",
        "RECAPTCHA_SCORE_THRESHOLD",
        "RECAPTCHA_TIMEOUT_MS",
        "RECAPTCHA_EXPECTED_HOSTNAME",
        "RECAPTCHA_PRIVATE_KEY",
        "FIRMWARE_DOWNLOAD_GATE_ENABLED",
        "FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS",
        "FIRMWARE_DOWNLOAD_AD_GATE_ENABLED",
        "FIRMWARE_DOWNLOAD_AD_GATE_SECONDS",
        "FIRMWARE_DOWNLOAD_TOKEN_EXPIRY_MINUTES",
        "FIRMWARE_DOWNLOAD_REQUIRE_LOGIN",
        "FIRMWARE_DOWNLOAD_MAX_PER_DAY",
        "FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION",
        "FIRMWARE_DOWNLOAD_LINK_ENCRYPTION",
        "FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS",
    }
)


@receiver(post_save, sender=SiteSettings)
@receiver(post_delete, sender=SiteSettings)
@receiver(post_save, sender=VerificationMetaTag)
@receiver(post_delete, sender=VerificationMetaTag)
@receiver(post_save, sender=VerificationFile)
@receiver(post_delete, sender=VerificationFile)
def invalidate_site_settings_cache(sender: type[Model], **kwargs: Any) -> None:
    """Clear now for this process and again on commit for concurrent readers."""
    clear_site_settings_cache()
    transaction.on_commit(clear_site_settings_cache)
    logger.debug("Site settings cache invalidated by %s", sender.__name__)


@receiver(setting_changed)
def invalidate_on_settings_override(setting: str, **kwargs: Any) -> None:
    if setting in WATCHED_SETTINGS:
        clear_site_settings_cache()
