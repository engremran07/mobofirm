"""The single accessor for runtime site settings.

``get_site_settings()`` returns an immutable snapshot that merges the admin-edited
``SiteSettings`` row with the environment (Django settings) defaults. The snapshot
is cached under a versioned key; saving or deleting any settings model rotates the
version (see ``signals``), which works on every cache backend.

Secrets (Gmail/SMTP passwords, the reCAPTCHA private key) are never part of the
snapshot; use :func:`get_email_runtime_config` and :func:`get_recaptcha_secret`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.http import HttpRequest
from django.utils import timezone

if TYPE_CHECKING:
    from .models import SiteSettings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300
VERSION_KEY = "site_settings:version"
SNAPSHOT_KEY = "site_settings:snapshot:{version}"
RECAPTCHA_MODES = frozenset({"v2", "v3"})


@dataclass(frozen=True, slots=True)
class SocialLink:
    key: str
    label: str
    url: str


@dataclass(frozen=True, slots=True)
class DownloadPolicy:
    gate_enabled: bool
    countdown_seconds: int
    ad_gate_enabled: bool
    ad_gate_seconds: int
    token_expiry_minutes: int
    require_login: bool
    max_per_day: int
    hotlink_protection: bool
    link_encryption: bool
    link_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class RecaptchaConfig:
    enabled: bool
    mode: str
    site_key: str
    secret_configured: bool
    score_threshold: float
    timeout_ms: int
    expected_hostname: str


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    ga4_id: str
    gtm_id: str

    @property
    def enabled(self) -> bool:
        return bool(self.ga4_id or self.gtm_id)


@dataclass(frozen=True, slots=True)
class EmailSummary:
    mode: str
    from_email: str
    credentials_configured: bool


@dataclass(frozen=True, slots=True)
class SiteSettingsSnapshot:
    site_name: str
    site_header: str
    site_description: str
    site_url: str
    contact_email: str
    copyright_text: str
    logo_url: str
    dark_logo_url: str
    favicon_url: str
    og_image_url: str
    primary_color: str
    secondary_color: str
    twitter_handle: str
    social_links: tuple[SocialLink, ...]
    default_language: str
    timezone: str
    maintenance_mode: bool
    maintenance_message: str
    force_https: bool
    require_profile_completion: bool
    email_verification_code_length: int
    email_verification_code_type: str
    download: DownloadPolicy
    recaptcha: RecaptchaConfig
    analytics: AnalyticsConfig
    email: EmailSummary
    meta_tags: tuple[tuple[str, str], ...]
    verification_filenames: tuple[str, ...]
    version: str

    @property
    def has_dark_logo(self) -> bool:
        return bool(self.dark_logo_url) and self.dark_logo_url != self.logo_url

    @property
    def has_custom_theme(self) -> bool:
        return bool(self.primary_color or self.secondary_color or self.has_dark_logo)


@dataclass(frozen=True, slots=True)
class EmailRuntimeConfig:
    """Decrypted mail credentials for the runtime email backend. Never cache this."""

    mode: str
    from_email: str
    gmail_username: str
    gmail_app_password: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_use_tls: bool
    smtp_use_ssl: bool


def _text(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or default)


def _flag(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def _number(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def _first_bool(stored: bool | None, name: str, default: bool) -> bool:
    return _flag(name, default) if stored is None else stored


def _first_int(stored: int | None, name: str, default: int) -> int:
    return _number(name, default) if stored is None else stored


def _file_url(field_file: object, fallback: str) -> str:
    if field_file:
        return str(getattr(field_file, "url", "") or fallback)
    return fallback


def _download_policy(row: SiteSettings) -> DownloadPolicy:
    return DownloadPolicy(
        gate_enabled=_first_bool(
            row.download_gate_enabled, "FIRMWARE_DOWNLOAD_GATE_ENABLED", True
        ),
        countdown_seconds=_first_int(
            row.download_countdown_seconds, "FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS", 10
        ),
        ad_gate_enabled=_first_bool(
            row.download_ad_gate_enabled, "FIRMWARE_DOWNLOAD_AD_GATE_ENABLED", False
        ),
        ad_gate_seconds=_first_int(
            row.download_ad_gate_seconds, "FIRMWARE_DOWNLOAD_AD_GATE_SECONDS", 30
        ),
        token_expiry_minutes=_first_int(
            row.download_token_expiry_minutes,
            "FIRMWARE_DOWNLOAD_TOKEN_EXPIRY_MINUTES",
            30,
        ),
        require_login=_first_bool(
            row.download_require_login, "FIRMWARE_DOWNLOAD_REQUIRE_LOGIN", False
        ),
        max_per_day=_first_int(
            row.download_max_per_day, "FIRMWARE_DOWNLOAD_MAX_PER_DAY", 0
        ),
        hotlink_protection=_first_bool(
            row.download_hotlink_protection,
            "FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION",
            True,
        ),
        link_encryption=_first_bool(
            row.download_link_encryption, "FIRMWARE_DOWNLOAD_LINK_ENCRYPTION", True
        ),
        link_ttl_seconds=_first_int(
            row.download_link_ttl_seconds, "FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS", 600
        ),
    )


def _recaptcha_config(row: SiteSettings) -> RecaptchaConfig:
    if row.recaptcha_mode:
        mode = row.recaptcha_mode
        enabled = mode in RECAPTCHA_MODES
    else:
        mode = _text("RECAPTCHA_MODE", "off").lower()
        enabled = _flag("RECAPTCHA_ENABLED") and mode in RECAPTCHA_MODES
    score = row.recaptcha_score_threshold
    return RecaptchaConfig(
        enabled=enabled,
        mode=mode if enabled else "off",
        site_key=row.recaptcha_site_key or _text("RECAPTCHA_SITE_KEY"),
        secret_configured=row.has_secret("recaptcha_private_key")
        or bool(_text("RECAPTCHA_PRIVATE_KEY")),
        score_threshold=(
            float(getattr(settings, "RECAPTCHA_SCORE_THRESHOLD", 0.5))
            if score is None
            else score
        ),
        timeout_ms=_first_int(row.recaptcha_timeout_ms, "RECAPTCHA_TIMEOUT_MS", 3000),
        expected_hostname=row.recaptcha_expected_hostname
        or _text("RECAPTCHA_EXPECTED_HOSTNAME"),
    )


def _public_contact(value: str) -> str:
    # The documented placeholder domain is never shown to visitors.
    return "" if value.lower().endswith(".invalid") else value


def _build_snapshot(
    row: SiteSettings,
    meta_tags: tuple[tuple[str, str], ...],
    filenames: tuple[str, ...],
    version: str,
) -> SiteSettingsSnapshot:
    site_name = row.site_name or _text("SITE_NAME", "Site")
    email_credentials = (
        row.has_secret("gmail_app_password")
        if row.email_mode == row.EmailMode.GMAIL
        else bool(row.smtp_host)
    )
    return SiteSettingsSnapshot(
        site_name=site_name,
        site_header=row.site_header or site_name,
        site_description=row.site_description or _text("SITE_DESCRIPTION"),
        site_url=(row.site_url or _text("SITE_URL")).rstrip("/"),
        contact_email=_public_contact(row.contact_email or _text("SITE_CONTACT_EMAIL")),
        copyright_text=row.copyright_text
        or f"\N{COPYRIGHT SIGN} {timezone.now().year} {site_name}",
        logo_url=_file_url(row.logo, _text("SITE_LOGO_URL")),
        dark_logo_url=_file_url(
            row.dark_logo,
            _file_url(row.logo, _text("SITE_DARK_LOGO_URL") or _text("SITE_LOGO_URL")),
        ),
        favicon_url=_file_url(row.favicon, _text("SITE_FAVICON_URL")),
        og_image_url=_file_url(row.og_image, ""),
        primary_color=row.primary_color,
        secondary_color=row.secondary_color,
        twitter_handle=row.twitter_handle,
        social_links=tuple(
            SocialLink(key=field.removeprefix("social_"), label=label, url=url)
            for field, label in row.SOCIAL_FIELDS
            if (url := getattr(row, field))
        ),
        default_language=row.default_language or _text("LANGUAGE_CODE", "en-us"),
        timezone=row.timezone or _text("TIME_ZONE", "UTC"),
        maintenance_mode=_flag("MAINTENANCE_MODE") or row.maintenance_mode,
        maintenance_message=row.maintenance_message,
        force_https=_flag("FORCE_HTTPS") or row.force_https,
        require_profile_completion=_first_bool(
            row.require_profile_completion, "REQUIRE_PROFILE_COMPLETION", False
        ),
        email_verification_code_length=row.email_verification_code_length,
        email_verification_code_type=row.email_verification_code_type,
        download=_download_policy(row),
        recaptcha=_recaptcha_config(row),
        analytics=AnalyticsConfig(
            ga4_id=row.google_analytics_id, gtm_id=row.google_tag_manager_id
        ),
        email=EmailSummary(
            mode=row.email_mode,
            from_email=row.from_email or _text("DEFAULT_FROM_EMAIL"),
            credentials_configured=email_credentials,
        ),
        meta_tags=meta_tags,
        verification_filenames=filenames,
        version=version,
    )


def _load_snapshot(version: str) -> SiteSettingsSnapshot | None:
    """Build the snapshot from the database; ``None`` when the schema is missing."""
    from .models import SiteSettings, VerificationFile, VerificationMetaTag

    try:
        with transaction.atomic():
            row = SiteSettings.objects.filter(
                pk=SiteSettings.singleton_instance_id
            ).first()
            tags = tuple(
                VerificationMetaTag.objects.filter(is_active=True).values_list(
                    "name", "content"
                )
            )
            names = tuple(
                VerificationFile.objects.filter(is_active=True).values_list(
                    "filename", flat=True
                )
            )
    except DatabaseError:
        logger.warning(
            "Site settings table is unavailable; using environment defaults.",
            exc_info=True,
        )
        return None
    # An absent row is the pristine state before the first save: model defaults.
    return _build_snapshot(row or SiteSettings(), tags, names, version)


def _current_version() -> str:
    version = cache.get(VERSION_KEY)
    if version is None:
        cache.add(VERSION_KEY, uuid.uuid4().hex, None)
        version = cache.get(VERSION_KEY) or "0"
    return str(version)


def clear_site_settings_cache() -> None:
    """Invalidate the snapshot everywhere the cache is shared."""
    cache.set(VERSION_KEY, uuid.uuid4().hex, None)


def get_site_settings() -> SiteSettingsSnapshot:
    version = _current_version()
    key = SNAPSHOT_KEY.format(version=version)
    cached = cache.get(key)
    if isinstance(cached, SiteSettingsSnapshot):
        return cached
    snapshot = _load_snapshot(version)
    if snapshot is None:
        from .models import SiteSettings

        return _build_snapshot(SiteSettings(), (), (), "unavailable")
    cache.set(key, snapshot, CACHE_TTL_SECONDS)
    return snapshot


def _stored_row() -> SiteSettings:
    from .models import SiteSettings

    return (
        SiteSettings.objects.filter(pk=SiteSettings.singleton_instance_id).first()
        or SiteSettings()
    )


def get_email_runtime_config() -> EmailRuntimeConfig:
    """Mail credentials for the runtime backend, decrypted on demand (uncached)."""
    try:
        row = _stored_row()
    except DatabaseError:
        logger.warning("Site settings table is unavailable; using environment mail.")
        from .models import SiteSettings

        row = SiteSettings()
    return EmailRuntimeConfig(
        mode=row.email_mode,
        from_email=row.from_email or _text("DEFAULT_FROM_EMAIL"),
        gmail_username=row.gmail_username,
        gmail_app_password=row.get_secret("gmail_app_password"),
        smtp_host=row.smtp_host,
        smtp_port=row.smtp_port,
        smtp_username=row.smtp_username,
        smtp_password=row.get_secret("smtp_password"),
        smtp_use_tls=row.smtp_use_tls,
        smtp_use_ssl=row.smtp_use_ssl,
    )


def get_recaptcha_secret() -> str:
    """The reCAPTCHA private key: the stored (encrypted) key, else the environment."""
    try:
        stored = _stored_row().get_secret("recaptcha_private_key")
    except DatabaseError:
        logger.warning("Site settings table is unavailable; using the environment key.")
        stored = ""
    return stored or _text("RECAPTCHA_PRIVATE_KEY")


def site_origin(request: HttpRequest | None = None) -> str:
    """Scheme and host of the public site; the configured site URL wins."""
    snapshot = get_site_settings()
    configured = urlsplit(snapshot.site_url)
    if configured.scheme in {"http", "https"} and configured.netloc:
        return f"{configured.scheme}://{configured.netloc}"
    if request is None:
        return ""
    scheme = "https" if snapshot.force_https else request.scheme
    return f"{scheme}://{request.get_host()}"


def absolute_url(path: str, request: HttpRequest | None = None) -> str:
    """Make a site path absolute; absolute http(s) URLs are returned unchanged."""
    parts = urlsplit(path)
    if parts.scheme in {"http", "https"} and parts.netloc:
        return path
    origin = site_origin(request)
    if not origin or not path.startswith("/") or path.startswith("//"):
        return ""
    return f"{origin}{path}"


def public_settings(snapshot: SiteSettingsSnapshot | None = None) -> dict[str, object]:
    """Non-sensitive keys safe to publish to anonymous visitors."""
    snap = snapshot or get_site_settings()
    return {
        "site_name": snap.site_name,
        "site_description": snap.site_description,
        "site_url": snap.site_url,
        "logo_url": snap.logo_url,
        "dark_logo_url": snap.dark_logo_url,
        "favicon_url": snap.favicon_url,
        "contact_email": snap.contact_email,
        "copyright_text": snap.copyright_text,
        "default_language": snap.default_language,
        "social_links": [
            {"key": link.key, "label": link.label, "url": link.url}
            for link in snap.social_links
        ],
        "maintenance_mode": snap.maintenance_mode,
        "recaptcha": {
            "enabled": snap.recaptcha.enabled,
            "mode": snap.recaptcha.mode,
            "site_key": snap.recaptcha.site_key,
        },
        "analytics_enabled": snap.analytics.enabled,
    }


def build_theme_css(snapshot: SiteSettingsSnapshot) -> str:
    """Light-theme accent overrides and dark-logo switching.

    The dark theme keeps its designed palette; only the logo swaps with it.
    """
    rules: list[str] = []
    declarations: list[str] = []
    if snapshot.primary_color:
        red, green, blue = (
            int(snapshot.primary_color[i : i + 2], 16) for i in (1, 3, 5)
        )
        declarations += [
            f"--mf-accent: {snapshot.primary_color};",
            f"--mf-accent-strong: {snapshot.primary_color};",
            f"--mf-focus: {snapshot.primary_color};",
            f"--bs-link-color-rgb: {red}, {green}, {blue};",
            f"--bs-primary-rgb: {red}, {green}, {blue};",
        ]
    if snapshot.secondary_color:
        declarations.append(f"--mf-accent-cool: {snapshot.secondary_color};")
    if declarations:
        body = "\n  ".join(declarations)
        rules.append(f':root[data-bs-theme="light"] {{\n  {body}\n}}')
    if snapshot.has_dark_logo:
        rules += [
            ".site-logo-dark { display: none; }",
            ':root[data-bs-theme="dark"] .site-logo-light { display: none; }',
            ':root[data-bs-theme="dark"] .site-logo-dark { display: inline-block; }',
        ]
    return "\n".join(rules) + "\n" if rules else ""
