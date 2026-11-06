"""Post-commit content observers connected from registered content sources."""

from __future__ import annotations

import logging
import threading
from functools import partial
from typing import Any

from django.apps import apps
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save, pre_save

from . import jobs
from .contentsources import ContentSource, content_sources
from .services.internal_linking.engine import refresh_linkable_entity

logger = logging.getLogger(__name__)
_previous = threading.local()


def _state(source: ContentSource, instance: Any) -> tuple[str, bool]:
    return source.url(instance), bool(source.is_public(instance))


def _previous_store() -> dict[tuple[str, str], tuple[str, bool]]:
    if not hasattr(_previous, "values"):
        _previous.values = {}
    return _previous.values


def _remember(sender: type[models.Model], instance: Any, **kwargs: Any) -> None:
    source = content_sources().get(sender._meta.label_lower)
    if source is None or instance.pk is None:
        return
    stored = sender._default_manager.filter(pk=instance.pk).first()
    if stored is not None:
        _previous_store()[(source.label, str(instance.pk))] = _state(source, stored)


def _saved(sender: type[models.Model], instance: Any, **kwargs: Any) -> None:
    source = content_sources().get(sender._meta.label_lower)
    if source is None:
        return
    key = (source.label, str(instance.pk))
    previous = _previous_store().pop(key, None)
    url, public = _state(source, instance)
    if source.linkable is not None:
        info = source.linkable(instance)
        if info is not None:
            refresh_linkable_entity(
                instance, title=info.title, url=info.url, keywords=info.keywords
            )
    if previous == (url, public) and not source.notify_on_edit:
        return
    transaction.on_commit(
        partial(
            jobs.record_content_change,
            source.label,
            str(instance.pk),
            url=url,
            public=public,
            previous=previous,
        )
    )


def _deleted(sender: type[models.Model], instance: Any, **kwargs: Any) -> None:
    source = content_sources().get(sender._meta.label_lower)
    if source is None:
        return
    url, was_public = _state(source, instance)
    transaction.on_commit(
        partial(
            jobs.record_content_change,
            source.label,
            str(instance.pk),
            url=url,
            public=False,
            previous=(url, was_public),
        )
    )


def connect_content_sources() -> None:
    """Connect observers for every registered source whose model is installed."""
    for label in content_sources():
        try:
            model = apps.get_model(label)
        except LookupError:
            logger.warning("SEO content source %s is not an installed model.", label)
            continue
        uid = f"seo-content-{label}"
        pre_save.connect(_remember, sender=model, dispatch_uid=f"{uid}-pre")
        post_save.connect(_saved, sender=model, dispatch_uid=f"{uid}-save")
        post_delete.connect(_deleted, sender=model, dispatch_uid=f"{uid}-delete")
