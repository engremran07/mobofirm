"""
apps.core.signals
-----------------
Seeds singleton settings so the admin never asks to create configs.
This runs post-migrate and is defensive: if an app is missing or migrations
are not applied yet, it quietly skips.
"""

from __future__ import annotations

import logging

from django.apps import apps as django_apps
from django.db.models.signals import post_migrate
from django.dispatch import receiver

logger = logging.getLogger(__name__)

SINGLETON_MODELS = (
    "core.AppRegistry",
    "devices.DeviceConfig",
    "ads.AdsSettings",
    "tags.TagsSettings",
    "comments.CommentSettings",
    "blog.BlogSettings",
    "users.UsersSettings",
    "seo.SEOSettings",
    "site_settings.SiteSettings",
)

DEFAULT_APP_POLICY = {
    "name": "default",
}


def ensure_singletons() -> None:
    """Create every installed singleton settings row (idempotent)."""
    for model_path in SINGLETON_MODELS:
        app_label, model_name = model_path.split(".", maxsplit=1)
        if not django_apps.is_installed(f"apps.{app_label}"):
            continue
        model = django_apps.get_model(app_label, model_name)
        if model is not None and hasattr(model, "get_solo"):
            model.get_solo()

    if django_apps.is_installed("apps.devices"):
        from apps.devices.models import AppPolicy

        AppPolicy.objects.get_or_create(
            name=DEFAULT_APP_POLICY["name"], defaults=DEFAULT_APP_POLICY
        )


@receiver(post_migrate)
def seed_singletons(sender, **kwargs):  # pragma: no cover - signal hook
    ensure_singletons()


# ============================================================================
# Cross-App Communication Signals
# ============================================================================
# These signals enable loose coupling between apps without circular dependencies.
# Apps emit signals instead of calling each other's code directly.

from django.dispatch import Signal

# Blog signals
blog_post_published = Signal()  # args: post, user
blog_post_updated = Signal()  # args: post, user
blog_post_deleted = Signal()  # args: post_id, user

# Comment signals
comment_created = Signal()  # args: comment, user
comment_approved = Signal()  # args: comment
comment_flagged = Signal()  # args: comment, reason

# User signals
user_registered = Signal()  # args: user, request
user_profile_updated = Signal()  # args: user
user_login_success = Signal()  # args: user, request

# SEO signals
seo_metadata_requested = Signal()  # args: content_object, force_regenerate
seo_sitemap_updated = Signal()  # args: sitemap_type

# Device signals
device_fingerprint_created = Signal()  # args: device, user
device_suspicious_activity = Signal()  # args: device, activity_type

# Content signals
content_viewed = Signal()  # args: content_object, user, request
content_shared = Signal()  # args: content_object, platform, user

# Notification signals
notification_send_requested = (
    Signal()
)  # args: user_ids, title, message, action_type, priority
