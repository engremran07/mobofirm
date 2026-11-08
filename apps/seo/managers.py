"""Typed access to a model's default manager through the public ``_meta`` API."""

from __future__ import annotations

from typing import Any

from django.db import models


def default_manager(model: type[models.Model]) -> models.Manager[Any]:
    """The model's default manager; every concrete model has one."""
    manager = model._meta.default_manager
    if manager is None:
        raise LookupError(f"{model._meta.label} has no default manager.")
    return manager
