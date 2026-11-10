"""Runtime, admin-editable site settings.

``SiteSettings`` is the single owner of the fields listed in
``docs/04-backend/site-settings.md``. Settings owned by another app (device limits,
user sign-up/MFA/login limits, blog, comments, ads, SEO and the feature registry)
are deliberately not duplicated here.

Inheritance rule: a blank text value or a ``NULL`` number/choice/boolean means
"use the environment (Django settings) default"; only deliberate overrides are
stored. Read everything through ``apps.site_settings.services.get_site_settings``.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from solo.models import SingletonModel

from . import validators
from .crypto import decrypt_secret, encrypt_secret
from .editor_models import EditorImage, EditorProfile

SecretName = Literal["gmail_app_password", "smtp_password", "recaptcha_private_key"]
SECRET_NAMES: tuple[SecretName, ...] = (
    "gmail_app_password",
    "smtp_password",
    "recaptcha_private_key",
)

MIN_CONTRAST = 4.5


class SiteSettings(SingletonModel):
    class EmailMode(models.TextChoices):
        ENVIRONMENT = "environment", "Use the environment configuration"
        GMAIL = "gmail", "Gmail SMTP (app password)"
        SMTP = "smtp", "Custom SMTP server"

    class RecaptchaMode(models.TextChoices):
        INHERIT = "", "Use the environment configuration"
        OFF = "off", "Off"
        V2 = "v2", "reCAPTCHA v2 (checkbox)"
        V3 = "v3", "reCAPTCHA v3 (score)"

    class CodeType(models.TextChoices):
        NUMERIC = "numeric", "Numeric"
        ALPHANUMERIC = "alphanumeric", "Alphanumeric"

    # Identity and branding -------------------------------------------------
    site_name = models.CharField(
        max_length=100, blank=True, default="", help_text="Blank uses SITE_NAME."
    )
    site_header = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Title shown in the admin header. Blank uses the site name.",
    )
    site_description = models.TextField(
        blank=True, default="", help_text="Blank uses SITE_DESCRIPTION."
    )
    logo = models.ImageField(
        upload_to="branding/",
        blank=True,
        validators=[validators.validate_image_size],
        help_text="Primary logo (PNG, JPEG, WebP or GIF, 2 MiB maximum).",
    )
    dark_logo = models.ImageField(
        upload_to="branding/",
        blank=True,
        validators=[validators.validate_image_size],
        help_text="Logo for dark backgrounds; the primary logo is used when empty.",
    )
    favicon = models.ImageField(
        upload_to="branding/",
        blank=True,
        validators=[validators.validate_image_size],
        help_text="Browser tab icon (PNG or ICO).",
    )
    primary_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        validators=[validators.hex_color_validator],
        help_text="Accent colour of the light theme. Blank keeps the design default.",
    )
    secondary_color = models.CharField(
        max_length=7,
        blank=True,
        default="",
        validators=[validators.hex_color_validator],
        help_text="Secondary accent of the light theme. Blank keeps the default.",
    )

    # Public URL, contact, social and share defaults -------------------------
    site_url = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_site_origin],
        help_text="Public origin, for example https://example.com. Blank uses SITE_URL.",
    )
    contact_email = models.EmailField(
        blank=True,
        default="",
        help_text="Public contact address. Blank uses SITE_CONTACT_EMAIL.",
    )
    copyright_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Footer text. Blank renders '© <year> <site name>'.",
    )
    social_twitter = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_facebook = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_instagram = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_github = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_youtube = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_linkedin = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    social_telegram = models.URLField(
        max_length=255,
        blank=True,
        default="",
        validators=[validators.validate_https_url],
    )
    og_image = models.ImageField(
        upload_to="branding/",
        blank=True,
        validators=[validators.validate_image_size],
        help_text="Default share image (1200x630 recommended); used by the SEO app.",
    )
    twitter_handle = models.CharField(
        max_length=15,
        blank=True,
        default="",
        validators=[validators.twitter_handle_validator],
        help_text="Handle for twitter:site, without @; used by the SEO app.",
    )

    # Localization --------------------------------------------------------------
    default_language = models.CharField(
        max_length=10,
        blank=True,
        default="",
        validators=[validators.validate_language_code],
        help_text="Language for visitors who send no language preference. "
        "Blank uses LANGUAGE_CODE.",
    )
    timezone = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[validators.validate_timezone_name],
        help_text="IANA time zone used to display dates. Blank uses TIME_ZONE.",
    )

    # Operations -----------------------------------------------------------------
    maintenance_mode = models.BooleanField(
        default=False,
        help_text="Visitors see a 503 page; staff, sign-in and health probes stay "
        "reachable. MAINTENANCE_MODE=true in the environment also enables it.",
    )
    maintenance_message = models.CharField(
        max_length=300,
        blank=True,
        default="",
        help_text="Plain text shown on the maintenance page.",
    )
    force_https = models.BooleanField(
        default=False,
        help_text="Build absolute URLs with https. This cannot weaken the SECURE_* "
        "settings; redirects and HSTS are controlled by the deployment settings.",
    )

    # Email (secrets are write-only and encrypted) -----------------------------
    email_mode = models.CharField(
        max_length=16, choices=EmailMode.choices, default=EmailMode.ENVIRONMENT
    )
    from_email = models.EmailField(
        blank=True,
        default="",
        help_text="Sender address. Blank uses DEFAULT_FROM_EMAIL.",
    )
    gmail_username = models.EmailField(blank=True, default="")
    gmail_app_password_encrypted = models.TextField(
        blank=True, default="", editable=False
    )
    smtp_host = models.CharField(max_length=255, blank=True, default="")
    smtp_port = models.PositiveIntegerField(
        default=587,
        validators=[MinValueValidator(1), MaxValueValidator(65535)],
    )
    smtp_username = models.CharField(max_length=255, blank=True, default="")
    smtp_password_encrypted = models.TextField(blank=True, default="", editable=False)
    smtp_use_tls = models.BooleanField(default=True)
    smtp_use_ssl = models.BooleanField(default=False)

    # reCAPTCHA --------------------------------------------------------------------
    recaptcha_mode = models.CharField(
        max_length=3,
        blank=True,
        choices=RecaptchaMode.choices,
        default=RecaptchaMode.INHERIT,
    )
    recaptcha_site_key = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Public key; blank uses RECAPTCHA_SITE_KEY.",
    )
    recaptcha_private_key_encrypted = models.TextField(
        blank=True, default="", editable=False
    )
    recaptcha_score_threshold = models.FloatField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text="v3 only. Blank uses RECAPTCHA_SCORE_THRESHOLD.",
    )
    recaptcha_timeout_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(500), MaxValueValidator(30000)],
        help_text="Blank uses RECAPTCHA_TIMEOUT_MS.",
    )
    recaptcha_expected_hostname = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Reject tokens issued for another host. Blank uses "
        "RECAPTCHA_EXPECTED_HOSTNAME.",
    )

    # Account security owned by this app ---------------------------------------
    require_profile_completion = models.BooleanField(
        null=True,
        blank=True,
        help_text="Social-login users must finish their profile first. "
        "Unknown uses REQUIRE_PROFILE_COMPLETION.",
    )
    email_verification_code_length = models.PositiveSmallIntegerField(
        default=6, validators=[MinValueValidator(4), MaxValueValidator(12)]
    )
    email_verification_code_type = models.CharField(
        max_length=12, choices=CodeType.choices, default=CodeType.ALPHANUMERIC
    )

    # Firmware download gate (NULL inherits FIRMWARE_DOWNLOAD_*) ---------------
    download_gate_enabled = models.BooleanField(null=True, blank=True)
    download_countdown_seconds = models.PositiveIntegerField(
        null=True, blank=True, validators=[MaxValueValidator(120)]
    )
    download_ad_gate_enabled = models.BooleanField(null=True, blank=True)
    download_ad_gate_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(300)],
    )
    download_token_expiry_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
    )
    download_require_login = models.BooleanField(null=True, blank=True)
    download_max_per_day = models.PositiveIntegerField(
        null=True, blank=True, help_text="0 means unlimited."
    )
    download_hotlink_protection = models.BooleanField(null=True, blank=True)
    download_link_encryption = models.BooleanField(null=True, blank=True)
    download_link_ttl_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(30), MaxValueValidator(86400)],
    )

    # Analytics (loaded through a same-origin script; off unless an ID is set) --
    google_analytics_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        validators=[validators.ga4_id_validator],
        help_text="GA4 measurement ID (G-XXXX). Leave blank for no analytics.",
    )
    google_tag_manager_id = models.CharField(
        max_length=32,
        blank=True,
        default="",
        validators=[validators.gtm_id_validator],
        help_text="GTM container ID (GTM-XXXX). Leave blank for no tag manager.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    SOCIAL_FIELDS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("social_twitter", "X (Twitter)"),
        ("social_facebook", "Facebook"),
        ("social_instagram", "Instagram"),
        ("social_github", "GitHub"),
        ("social_youtube", "YouTube"),
        ("social_linkedin", "LinkedIn"),
        ("social_telegram", "Telegram"),
    )

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self) -> str:
        return self.site_name or "Site settings"

    # Secrets ---------------------------------------------------------------------
    def set_secret(self, name: SecretName, plaintext: str) -> None:
        setattr(self, f"{name}_encrypted", encrypt_secret(plaintext))

    def get_secret(self, name: SecretName) -> str:
        return decrypt_secret(getattr(self, f"{name}_encrypted"))

    def has_secret(self, name: SecretName) -> bool:
        return bool(getattr(self, f"{name}_encrypted"))

    # Validation ------------------------------------------------------------------
    def clean(self) -> None:
        super().clean()
        errors: dict[str, list[str]] = {}

        def add(field: str, message: str) -> None:
            errors.setdefault(field, []).append(message)

        self.site_url = self.site_url.rstrip("/")
        for field in ("primary_color", "secondary_color"):
            value = str(getattr(self, field))
            if value and re.fullmatch(validators.HEX_COLOR_PATTERN, value):
                value = value.lower()
                setattr(self, field, value)
                if validators.contrast_against_white(value) < MIN_CONTRAST:
                    add(
                        field,
                        "This colour is too light: it needs a contrast ratio of at "
                        f"least {MIN_CONTRAST}:1 against white.",
                    )
        if self.smtp_use_tls and self.smtp_use_ssl:
            add("smtp_use_ssl", "Choose either STARTTLS or SSL/TLS, not both.")
        if self.email_mode == self.EmailMode.GMAIL:
            if not self.gmail_username:
                add("gmail_username", "The Gmail address is required for Gmail mode.")
            if not self.has_secret("gmail_app_password"):
                add(
                    NON_FIELD_ERRORS,
                    "A Gmail app password is required for Gmail mode.",
                )
        if self.email_mode == self.EmailMode.SMTP and not self.smtp_host:
            add("smtp_host", "The SMTP host is required for custom SMTP mode.")
        if self.recaptcha_mode in {self.RecaptchaMode.V2, self.RecaptchaMode.V3}:
            if not (
                self.recaptcha_site_key or getattr(settings, "RECAPTCHA_SITE_KEY", "")
            ):
                add(
                    "recaptcha_site_key",
                    "The site key is required to enable reCAPTCHA.",
                )
            env_private_key: str = getattr(settings, "RECAPTCHA_PRIVATE_KEY", "")
            if not (self.has_secret("recaptcha_private_key") or env_private_key):
                add(
                    NON_FIELD_ERRORS,
                    "The private key is required to enable reCAPTCHA.",
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


class VerificationMetaTag(models.Model):
    """A ``<meta name=... content=...>`` tag emitted in the public page head."""

    site_settings = models.ForeignKey(
        SiteSettings, on_delete=models.CASCADE, related_name="meta_tags"
    )
    provider = models.CharField(max_length=50, help_text="For example Google or Bing.")
    name = models.CharField(max_length=100, validators=[validators.meta_name_validator])
    content = models.CharField(
        max_length=255, validators=[validators.meta_content_validator]
    )
    description = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("provider", "name")
        constraints = (
            models.UniqueConstraint(
                fields=["name", "content"], name="site_settings_unique_meta_tag"
            ),
        )

    def __str__(self) -> str:
        return f"{self.provider}: {self.name}"


class VerificationFile(models.Model):
    """A small ownership-verification file served at /site/verification/<name>/."""

    site_settings = models.ForeignKey(
        SiteSettings, on_delete=models.CASCADE, related_name="verification_files"
    )
    provider = models.CharField(max_length=50, help_text="For example Google or Bing.")
    file = models.FileField(
        upload_to="verification/", validators=[validators.validate_verification_upload]
    )
    filename = models.CharField(
        max_length=128,
        unique=True,
        blank=True,
        validators=[validators.validate_verification_filename],
        help_text="Public file name. Defaults to the uploaded file's name.",
    )
    description = models.CharField(max_length=255, blank=True, default="")
    is_active = models.BooleanField(default=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("provider", "filename")

    def __str__(self) -> str:
        return f"{self.provider}: {self.filename or '(unnamed)'}"

    def clean(self) -> None:
        super().clean()
        if not self.filename and self.file and self.file.name:
            self.filename = self.file.name.replace("\\", "/").rsplit("/", 1)[-1]
        if self.filename:
            try:
                validators.validate_verification_filename(self.filename)
            except ValidationError as exc:
                raise ValidationError({"filename": exc.messages}) from exc

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean()
        super().save(*args, **kwargs)


__all__ = [
    "SECRET_NAMES",
    "EditorImage",
    "EditorProfile",
    "SiteSettings",
    "VerificationFile",
    "VerificationMetaTag",
]
