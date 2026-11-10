"""Optional PostgreSQL ``pg_trgm`` support for typo-tolerant candidate lookup."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Final

from django.contrib.postgres.search import TrigramSimilarity
from django.db import DatabaseError, connection
from django.db.models import QuerySet
from django.db.models.functions import Greatest

logger = logging.getLogger(__name__)

TRIGRAM_THRESHOLD: Final[float] = 0.3
_STATE: dict[str, bool] = {}


def trigram_available() -> bool:
    """``True`` on PostgreSQL when the ``pg_trgm`` extension is installed."""
    if connection.vendor != "postgresql":
        return False
    if "available" not in _STATE:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
                _STATE["available"] = cursor.fetchone() is not None
        except DatabaseError:
            logger.warning("Could not look for pg_trgm; using Python typo matching")
            _STATE["available"] = False
    return _STATE["available"]


def reset_trigram_probe() -> None:
    """Forget the cached extension probe (tests, after ``CREATE EXTENSION``)."""
    _STATE.clear()


def trigram_candidates(
    queryset: QuerySet, fields: Iterable[str], tokens: tuple[str, ...]
) -> QuerySet:
    """Rows where some column is trigram-similar to some query word (PostgreSQL)."""
    scores = [TrigramSimilarity(name, token) for name in fields for token in tokens]
    best = scores[0] if len(scores) == 1 else Greatest(*scores)
    return queryset.annotate(_trigram_score=best).filter(
        _trigram_score__gte=TRIGRAM_THRESHOLD
    )
