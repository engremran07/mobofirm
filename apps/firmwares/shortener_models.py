"""Models of the external download link shortener (owned by ``firmwares``).

* :class:`ShortenerSettings`: the singleton the staff console edits (mode, provider,
  encrypted API key, timeout) plus the circuit-breaker and health-check state.
* :class:`DownloadShortLink` / :class:`DownloadShortLinkDay`: first-party ``/d/<code>/``
  links and their aggregate daily click counts (no IP, user agent or cookie).
* :class:`ThirdPartyShortLink`: cached provider result, idempotent per provider+target.
* :class:`ShortenerOutcome`: bounded audit trail of provider outcomes (never a secret).
* :class:`FirmwareShortenerOverride`: per-firmware choice that beats the brand and global
  mode. The per-brand choice is ``Brand.shortener_mode``.
"""

from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from solo.models import SingletonModel

from .crypto import decrypt_password, encrypt_password
from .shortener_constants import (
    CODE_LENGTH,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MAX_URL_LENGTH,
    HttpMethod,
    ProviderKind,
    Result,
    ShortenerMode,
    ShortenerOverride,
    Stage,
)


class ShortenerSettings(SingletonModel):
    """Global switch and third-party provider configuration (staff console)."""

    mode = models.CharField(
        max_length=16,
        choices=ShortenerMode.choices,
        default=ShortenerMode.OFF,
        help_text=(
            "Which link is shared for an external download. Off keeps today's plain "
            "link. The first-party link /d/<code>/ goes through the download gate "
            "(countdown and optional ad page) before the file host."
        ),
    )
    provider_kind = models.CharField(
        max_length=16,
        choices=ProviderKind.choices,
        default=ProviderKind.ISGD,
        help_text="Used by the third-party and both modes.",
    )
    provider_endpoint = models.CharField(
        max_length=MAX_URL_LENGTH,
        blank=True,
        default="",
        help_text=(
            "https endpoint for the monetized and generic kinds. Write {url} where "
            "the encoded link goes and {key} where the API key goes, e.g. "
            "https://shrinkme.io/api?api={key}&url={url}&format=text"
        ),
    )
    provider_method = models.CharField(
        max_length=4,
        choices=HttpMethod.choices,
        default=HttpMethod.GET,
        help_text="Generic JSON API only.",
    )
    provider_api_key_encrypted = models.CharField(
        max_length=512, blank=True, default="", editable=False
    )
    provider_auth_header = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Generic JSON API only: header that carries the key, e.g. Authorization.",
    )
    provider_auth_prefix = models.CharField(
        max_length=32,
        blank=True,
        default="",
        help_text="Text before the key in that header, e.g. 'Bearer ' (keep the space).",
    )
    provider_body_template = models.TextField(
        blank=True,
        default='{"url": "{url}"}',
        help_text=(
            "Generic JSON API with POST: a JSON object; {url} and {key} inside text "
            "values are replaced."
        ),
    )
    provider_response_path = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text=(
            "Where the short link is in a JSON answer, e.g. data.short_url. Leave "
            "blank to detect common names or a plain-text answer."
        ),
    )
    timeout_seconds = models.PositiveSmallIntegerField(
        default=DEFAULT_TIMEOUT_SECONDS,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_TIMEOUT_SECONDS)],
        help_text="Longest wait for the provider per request (1-10 seconds).",
    )

    # Runtime state (written by the service, never edited by hand).
    breaker_failures = models.PositiveIntegerField(default=0, editable=False)
    breaker_open_until = models.DateTimeField(null=True, blank=True, editable=False)
    cache_epoch = models.PositiveIntegerField(default=0, editable=False)
    last_check_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_check_ok = models.BooleanField(null=True, blank=True, editable=False)
    last_check_message = models.CharField(
        max_length=200, blank=True, default="", editable=False
    )

    class Meta:
        verbose_name = "Link shortener settings"
        verbose_name_plural = "Link shortener settings"

    def __str__(self) -> str:
        return "Link shortener settings"

    # --- write-only API key -------------------------------------------------

    @property
    def has_api_key(self) -> bool:
        return bool(self.provider_api_key_encrypted)

    def set_api_key(self, plaintext: str) -> None:
        """Store the key Fernet-encrypted (``ImproperlyConfigured`` without a key)."""
        self.provider_api_key_encrypted = (
            encrypt_password(plaintext) if plaintext else ""
        )

    def get_api_key(self) -> str:
        """The plaintext key, '' when none is stored. Never log or display it."""
        if not self.provider_api_key_encrypted:
            return ""
        return decrypt_password(self.provider_api_key_encrypted)

    @property
    def configuration_problem(self) -> str:
        """Why the third-party provider cannot be used ('' when it can)."""
        kind = self.provider_kind
        if kind in {ProviderKind.QUERY_API, ProviderKind.JSON_API}:
            if not self.provider_endpoint:
                return "The provider endpoint is not set."
            if kind == ProviderKind.QUERY_API and not self.has_api_key:
                return "The monetized shortener needs an API key."
            if (
                "{key}" in self.provider_endpoint
                or "{key}" in self.provider_body_template
            ) and not self.has_api_key:
                return "The endpoint uses {key} but no API key is stored."
        return ""


class DownloadShortLink(models.Model):
    """One first-party short code for one external firmware download.

    The code maps to ``(firmware_type, firmware_id)`` and the digest of the external
    URL it was created for. The destination is never stored: it is derived from the
    live, approved firmware and re-validated on every redirect.
    """

    code = models.CharField(max_length=CODE_LENGTH, unique=True)
    firmware_type = models.CharField(max_length=16)
    firmware_id = models.UUIDField()
    target_digest = models.CharField(
        max_length=64, help_text="sha256 of the external URL this code was made for."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["firmware_type", "firmware_id", "target_digest"],
                name="download_shortlink_one_per_target",
            )
        ]

    def __str__(self) -> str:
        return self.code


class DownloadShortLinkDay(models.Model):
    """Aggregate clicks of one short link on one day (no personal data)."""

    link = models.ForeignKey(
        DownloadShortLink, on_delete=models.CASCADE, related_name="days"
    )
    day = models.DateField()
    clicks = models.PositiveIntegerField(default=0)
    bot_clicks = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["-day"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["link", "day"], name="download_shortlink_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.link.code} {self.day}"


class ThirdPartyShortLink(models.Model):
    """Provider result cached per ``sha256(provider + endpoint + epoch + target)``."""

    cache_key = models.CharField(max_length=64, unique=True)
    provider_kind = models.CharField(max_length=16)
    target_url = models.CharField(max_length=MAX_URL_LENGTH)
    short_url = models.URLField(max_length=MAX_URL_LENGTH)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return self.short_url


class ShortenerOutcome(models.Model):
    """Audit row for a provider call or fallback. Holds no secret and no raw error."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    stage = models.CharField(max_length=16, choices=Stage.choices)
    provider_kind = models.CharField(max_length=16, blank=True, default="")
    result = models.CharField(max_length=16, choices=Result.choices)
    code = models.CharField(max_length=40, blank=True, default="")
    detail = models.CharField(max_length=200, blank=True, default="")
    firmware_type = models.CharField(max_length=16, blank=True, default="")
    firmware_id = models.UUIDField(null=True, blank=True)
    target_host = models.CharField(max_length=253, blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.stage} {self.result}"


class FirmwareShortenerOverride(models.Model):
    """Per-firmware shortening choice (beats the brand and the global mode)."""

    firmware_type = models.CharField(max_length=16)
    firmware_id = models.UUIDField()
    mode = models.CharField(
        max_length=16,
        choices=[
            choice
            for choice in ShortenerOverride.choices
            if choice[0] != ShortenerOverride.INHERIT
        ],
        default=ShortenerOverride.OFF,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-updated_at"]
        verbose_name = "Firmware shortener override"
        verbose_name_plural = "Firmware shortener overrides"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["firmware_type", "firmware_id"],
                name="firmware_shortener_override_unique",
            )
        ]

    def __str__(self) -> str:
        return f"{self.firmware_type}/{self.firmware_id}: {self.mode}"

    def clean(self) -> None:
        super().clean()
        from .catalog import PUBLIC_FIRMWARE_TYPES

        model_class = dict(PUBLIC_FIRMWARE_TYPES).get(self.firmware_type)
        if model_class is None:
            raise ValidationError({"firmware_type": "Unknown firmware type."})
        if not model_class.objects.filter(pk=self.firmware_id).exists():
            raise ValidationError({"firmware_id": "No firmware with this id."})
