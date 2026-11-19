"""Choices and limits of the download link shortener (no model imports).

Kept apart from the models so ``Brand`` can use the override choices without a
circular import.
"""

from __future__ import annotations

from typing import Final

from django.db import models


class ShortenerMode(models.TextChoices):
    """What the global switch does to a shared external download link."""

    OFF = "off", "Off (share the plain link)"
    FIRST_PARTY = "first_party", "First-party only (/d/<code>/)"
    THIRD_PARTY = "third_party", "Third-party only (provider chosen below)"
    BOTH = "both", "Both (first-party link, then shortened by the provider)"


class ShortenerOverride(models.TextChoices):
    """Per-brand / per-firmware choice that can differ from the global mode."""

    INHERIT = "inherit", "Inherit the global setting"
    OFF = "off", "None (never shorten)"
    FIRST_PARTY = "first_party", "Force first-party"
    THIRD_PARTY = "third_party", "Force third-party"
    BOTH = "both", "Force both"


class ProviderKind(models.TextChoices):
    ISGD = "isgd", "is.gd (no key)"
    TINYURL = "tinyurl", "TinyURL (no key)"
    QUERY_API = "query_api", "Monetized shortener (?api=KEY&url= style)"
    JSON_API = "json_api", "Generic JSON API (GET or POST)"


class HttpMethod(models.TextChoices):
    GET = "GET", "GET"
    POST = "POST", "POST"


class Stage(models.TextChoices):
    FIRST_PARTY = "first_party", "First-party"
    THIRD_PARTY = "third_party", "Third-party"
    HEALTH_CHECK = "health_check", "Health check"


class Result(models.TextChoices):
    CREATED = "created", "Created"
    FAILED = "failed", "Failed (fallback used)"
    BREAKER_OPEN = "breaker_open", "Circuit opened (provider paused)"
    OK = "ok", "Healthy"


# Short code: 8 characters of a 31-symbol alphabet without look-alikes (~8.5e11 values),
# drawn from ``secrets`` so codes cannot be enumerated.
CODE_ALPHABET: Final = "abcdefghjkmnpqrstuvwxyz23456789"
CODE_LENGTH: Final = 8
CODE_ATTEMPTS: Final = 5

# Circuit breaker of the third-party provider.
BREAKER_THRESHOLD: Final = 5
BREAKER_OPEN_SECONDS: Final = 900

MAX_OUTCOME_ROWS: Final = 2000
MAX_URL_LENGTH: Final = 500
DEFAULT_TIMEOUT_SECONDS: Final = 4
MAX_TIMEOUT_SECONDS: Final = 10
