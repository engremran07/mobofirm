"""Public contract of the marketplace for other apps (resolved via ``AppService``)."""

from __future__ import annotations

from apps.marketplace.services import is_active_seller

__all__ = ["is_active_seller"]
