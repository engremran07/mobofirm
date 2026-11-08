from __future__ import annotations

from collections.abc import Iterator

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clean_rate_limit_cache() -> Iterator[None]:
    """User ids repeat across rolled-back tests, so rate-limit counters must not leak."""
    cache.clear()
    yield
    cache.clear()
