from __future__ import annotations

import logging
import smtplib
from typing import TYPE_CHECKING, Any

from django.contrib import admin, messages
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.admin_base import (
    ModelAdmin,
    SingletonModelAdmin,
    TabularInline,
    action,
    image_preview,
)

from .crypto import SecretUnavailableError
from .forms import SiteSettingsAdminForm
from .models import (
    SECRET_NAMES,
    SiteSettings,
    VerificationFile,
    VerificationMetaTag,
)
from .services import get_site_settings

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldOpts

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("apps.site_settings.audit")


class VerificationMetaTagInline(TabularInline):
    model = VerificationMetaTag
    tab = True
    fields = ("provider", "name", "content", "description", "is_active")


class VerificationFileInline(TabularInline):
    model = VerificationFile
    tab = True
    fields = ("provider", "file", "filename", "description", "is_active")


@admin.register(VerificationMetaTag)
class VerificationMetaTagAdmin(ModelAdmin):
    list_display = ("provider", "name", "content", "is_active", "created_at")
    list_filter = ("is_active", "provider")
    search_fields = ("provider", "name", "description")
    list_editable = ("is_active",)
    fields = (
        "site_settings",
        "provider",
        "name",
        "content",
        "description",
        "is_active",
    )


@admin.register(VerificationFile)
class VerificationFileAdmin(ModelAdmin):
    list_display = ("provider", "filename", "is_active", "uploaded_at")
    list_filter = ("is_active", "provider")
    search_fields = ("provider", "filename", "description")
    list_editable = ("is_active",)
    fields = (
        "site_settings",
        "provider",
        "file",
        "filename",
        "description",
        "is_active",
    )


def _tab(title: str, *fields: str) -> tuple[str, _FieldOpts]:
    return (title, {"classes": ["tab"], "fields": fields})


@admin.register(SiteSettings)
class SiteSettingsAdmin(SingletonModelAdmin):
    form = SiteSettingsAdminForm
    inlines = (VerificationMetaTagInline, VerificationFileInline)
    actions_submit_line = ("send_test_email",)
    readonly_fields = (
        "logo_preview",
        "dark_logo_preview",
        "favicon_preview",
        "og_image_preview",
        "email_status",
        "effective_defaults",
    )
    fieldsets = (
        _tab(
            "Branding",
            "site_name",
            "site_header",
            "site_description",
            "logo",
            "logo_preview",
            "dark_logo",
            "dark_logo_preview",
            "favicon",
            "favicon_preview",
            "primary_color",
            "secondary_color",
            "effective_defaults",
        ),
        _tab("Site and contact", "site_url", "contact_email", "copyright_text"),
        _tab(
            "Social",
            "social_twitter",
            "social_facebook",
            "social_instagram",
            "social_github",
            "social_youtube",
            "social_linkedin",
            "social_telegram",
        ),
        _tab("SEO defaults", "og_image", "og_image_preview", "twitter_handle"),
        _tab("Localization", "default_language", "timezone"),
        _tab("Operations", "maintenance_mode", "maintenance_message", "force_https"),
        _tab(
            "Email",
            "email_status",
            "email_mode",
            "from_email",
            "gmail_username",
            "gmail_app_password",
            "clear_gmail_app_password",
            "smtp_host",
            "smtp_port",
            "smtp_username",
            "smtp_password",
            "clear_smtp_password",
            "smtp_use_tls",
            "smtp_use_ssl",
        ),
        _tab(
            "Security",
            "require_profile_completion",
            "email_verification_code_length",
            "email_verification_code_type",
        ),
        _tab(
            "Captcha",
            "recaptcha_mode",
            "recaptcha_site_key",
            "recaptcha_private_key",
            "clear_recaptcha_private_key",
            "recaptcha_score_threshold",
            "recaptcha_timeout_ms",
            "recaptcha_expected_hostname",
        ),
        _tab(
            "Download gate",
            "download_gate_enabled",
            "download_countdown_seconds",
            "download_ad_gate_enabled",
            "download_ad_gate_seconds",
            "download_token_expiry_minutes",
            "download_require_login",
            "download_max_per_day",
            "download_hotlink_protection",
            "download_link_encryption",
            "download_link_ttl_seconds",
        ),
        _tab("Analytics", "google_analytics_id", "google_tag_manager_id"),
    )

    @staticmethod
    def _preview(field_file: Any, label: str) -> str:
        if not field_file:
            return "No image uploaded."
        return image_preview(field_file.url, f"Current {label}")

    @admin.display(description="Logo preview")
    def logo_preview(self, obj: SiteSettings) -> str:
        return self._preview(obj.logo, "logo")

    @admin.display(description="Dark logo preview")
    def dark_logo_preview(self, obj: SiteSettings) -> str:
        return self._preview(obj.dark_logo, "dark logo")

    @admin.display(description="Favicon preview")
    def favicon_preview(self, obj: SiteSettings) -> str:
        return self._preview(obj.favicon, "favicon")

    @admin.display(description="Share image preview")
    def og_image_preview(self, obj: SiteSettings) -> str:
        return self._preview(obj.og_image, "share image")

    @admin.display(description="Effective values")
    def effective_defaults(self, _obj: SiteSettings) -> str:
        snapshot = get_site_settings()
        return format_html(
            "Name: <strong>{}</strong> &middot; URL: <strong>{}</strong> "
            "&middot; Blank fields above fall back to the environment.",
            snapshot.site_name,
            snapshot.site_url or "(not configured)",
        )

    @admin.display(description="Email status")
    def email_status(self, obj: SiteSettings) -> str:
        parts = [f"Mode: {obj.get_email_mode_display()}"]
        if obj.email_mode == SiteSettings.EmailMode.GMAIL:
            parts.append(
                "app password stored"
                if obj.has_secret("gmail_app_password")
                else "no app password"
            )
        elif obj.email_mode == SiteSettings.EmailMode.SMTP:
            parts.append(
                "password stored" if obj.has_secret("smtp_password") else "no password"
            )
        return " \u00b7 ".join(parts)

    def save_model(
        self, request: HttpRequest, obj: Model, form: ModelForm, change: bool
    ) -> None:
        skip = {*SECRET_NAMES, *(f"clear_{name}" for name in SECRET_NAMES)}
        changed = [name for name in form.changed_data if name not in skip]
        secrets = (
            form.changed_secret_names()
            if isinstance(form, SiteSettingsAdminForm)
            else []
        )
        super().save_model(request, obj, form, change)
        audit_logger.info(
            "Site settings changed by user %s: fields=%s secrets_updated=%s",
            request.user.pk,
            sorted(changed),
            secrets,
        )

    @action(description=_("Save and send test email"), permissions=["change"])
    def send_test_email(self, request: HttpRequest, _obj: SiteSettings) -> None:
        recipient = getattr(request.user, "email", "")
        if not recipient:
            self.message_user(
                request,
                "Your account has no email address to receive the test message.",
                messages.ERROR,
            )
            return
        snapshot = get_site_settings()
        message = EmailMessage(
            subject=f"[{snapshot.site_name}] Test email",
            body="This message confirms the site's email settings work.",
            from_email=snapshot.email.from_email or None,
            to=[recipient],
        )
        try:
            message.send()
        except (
            OSError,
            smtplib.SMTPException,
            ImproperlyConfigured,
            SecretUnavailableError,
        ) as exc:
            logger.error("Test email failed: %s", exc.__class__.__name__)
            self.message_user(
                request,
                f"The test email could not be sent ({exc.__class__.__name__}); "
                "check the server log.",
                messages.ERROR,
            )
            return
        self.message_user(
            request, f"A test email was sent to {recipient}.", messages.SUCCESS
        )
