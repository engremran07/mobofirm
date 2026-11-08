"""Signal receivers owned by the shop."""

from __future__ import annotations

from typing import Any

from django.apps import apps as django_apps

from apps.core.app_service import app_enabled


def assign_free_package(
    instance: Any, created: bool, raw: bool = False, **kwargs: Any
) -> None:
    """Every new account (allauth e-mail or social, admin, command) gets the free package.

    Runs in the transaction that creates the user, so an account never exists without
    its baseline once a default package is defined. A missing default package is not an
    error: ``backfill_free_package`` assigns it later.
    """
    if not created or raw or not django_apps.is_installed("apps.shop"):
        return
    if not app_enabled("shop"):
        return
    from .baseline import ensure_baseline_package

    ensure_baseline_package(instance)
