"""Shared fixtures for the users tests."""

from __future__ import annotations

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    """Rate-limit counters live in the cache; never carry them across tests."""
    cache.clear()
    yield
    cache.clear()
