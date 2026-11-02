"""
Social Authentication Provider Configuration Models

This module provides Admin Suite management for OAuth/social login providers.
Works alongside django-allauth's SocialApp model to provide a unified configuration interface.

Key Features:
- Encrypted credential storage
- OAuth flow management
- Provider status tracking
- Browser redirect flow support for OAuth completion
"""

from __future__ import annotations

import json
import uuid

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.sites.models import Site
from django.db import models
from django.utils import timezone

# Import encryption utilities from storage module
try:
    from apps.storage.models import get_encryption_key
except ImportError:
    import os

    def get_encryption_key():
        """Get or create encryption key for credentials."""
        key_path = os.path.join(settings.BASE_DIR, ".credentials_key")
        if os.path.exists(key_path):
            with open(key_path, "rb") as f:
                return f.read()
        key = Fernet.generate_key()
        with open(key_path, "wb") as f:
            f.write(key)
        return key


class SocialProviderConfig(models.Model):
    """
    Enhanced configuration for social authentication providers.

    This model extends allauth's SocialApp with:
    - Admin Suite integration
    - Encrypted credential storage
    - OAuth flow status tracking
    - Browser redirect flow support

    Supported providers:
    - Google OAuth 2.0 (requires browser OAuth)
    - Facebook Login (App ID + Secret)
    - Microsoft Azure AD (requires browser OAuth)
    - GitHub OAuth (Client ID + Secret)
    - Apple Sign In
    - Twitter/X OAuth 2.0
    - LinkedIn OAuth 2.0
    """

    PROVIDER_CHOICES = [
        ("google", "Google OAuth 2.0"),
        ("facebook", "Facebook Login"),
        ("microsoft", "Microsoft Azure AD"),
        ("github", "GitHub OAuth"),
        ("apple", "Apple Sign In"),
        ("twitter", "Twitter / X"),
        ("linkedin", "LinkedIn"),
        ("discord", "Discord"),
        ("slack", "Slack"),
    ]

    AUTH_FLOW_CHOICES = [
        ("api_credentials", "API Credentials Only (Client ID + Secret)"),
        ("browser_oauth", "Browser OAuth Required"),
        ("service_account", "Service Account (JSON)"),
    ]

    STATUS_CHOICES = [
        ("unconfigured", "Not Configured"),
        ("pending_oauth", "Pending OAuth Flow"),
        ("active", "Active"),
        ("error", "Configuration Error"),
        ("disabled", "Disabled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, choices=PROVIDER_CHOICES, unique=True)
    display_name = models.CharField(
        max_length=128, blank=True, help_text="Custom display name"
    )

    # Auth flow type
    auth_flow_type = models.CharField(
        max_length=32, choices=AUTH_FLOW_CHOICES, default="api_credentials"
    )

    # Encrypted credentials
    _client_id_encrypted = models.BinaryField(
        blank=True, null=True, db_column="client_id_encrypted"
    )
    _client_secret_encrypted = models.BinaryField(
        blank=True, null=True, db_column="client_secret_encrypted"
    )
    _additional_config_encrypted = models.BinaryField(
        blank=True, null=True, db_column="additional_config_encrypted"
    )

    # Provider-specific fields
    tenant_id = models.CharField(
        max_length=255, blank=True, help_text="Microsoft Azure Tenant ID"
    )
    team_id = models.CharField(max_length=64, blank=True, help_text="Apple Team ID")
    key_id = models.CharField(max_length=64, blank=True, help_text="Apple Key ID")

    # OAuth tokens (if browser flow was completed)
    _access_token_encrypted = models.BinaryField(
        blank=True, null=True, db_column="access_token_encrypted"
    )
    _refresh_token_encrypted = models.BinaryField(
        blank=True, null=True, db_column="refresh_token_encrypted"
    )
    token_expiry = models.DateTimeField(null=True, blank=True)

    # Status tracking
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default="unconfigured"
    )
    is_enabled = models.BooleanField(default=True)

    # Scopes
    scopes = models.JSONField(
        default=list, blank=True, help_text="OAuth scopes to request"
    )

    # Settings
    settings_json = models.JSONField(
        default=dict, blank=True, help_text="Provider-specific settings"
    )

    # Tracking
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    users_count = models.IntegerField(
        default=0, help_text="Users who signed up via this provider"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_social_configs",
    )

    class Meta:
        ordering = ["provider"]
        verbose_name = "Social Auth Provider"
        verbose_name_plural = "Social Auth Providers"

    def __str__(self):
        return f"{self.get_provider_display()} ({self.status})"  # type: ignore[attr-defined]

    def _get_cipher(self):
        """Get Fernet cipher for encryption/decryption."""
        return Fernet(get_encryption_key())

    # Encrypted field accessors
    def set_client_id(self, value: str):
        if not value:
            self._client_id_encrypted = None
            return
        cipher = self._get_cipher()
        self._client_id_encrypted = cipher.encrypt(value.encode())

    def get_client_id(self) -> str:
        if not self._client_id_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._client_id_encrypted)).decode()
        except Exception:
            return ""

    def set_client_secret(self, value: str):
        if not value:
            self._client_secret_encrypted = None
            return
        cipher = self._get_cipher()
        self._client_secret_encrypted = cipher.encrypt(value.encode())

    def get_client_secret(self) -> str:
        if not self._client_secret_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._client_secret_encrypted)).decode()
        except Exception:
            return ""

    def set_access_token(self, value: str):
        if not value:
            self._access_token_encrypted = None
            return
        cipher = self._get_cipher()
        self._access_token_encrypted = cipher.encrypt(value.encode())

    def get_access_token(self) -> str:
        if not self._access_token_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._access_token_encrypted)).decode()
        except Exception:
            return ""

    def set_refresh_token(self, value: str):
        if not value:
            self._refresh_token_encrypted = None
            return
        cipher = self._get_cipher()
        self._refresh_token_encrypted = cipher.encrypt(value.encode())

    def get_refresh_token(self) -> str:
        if not self._refresh_token_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._refresh_token_encrypted)).decode()
        except Exception:
            return ""

    def set_additional_config(self, config: dict):
        if not config:
            self._additional_config_encrypted = None
            return
        cipher = self._get_cipher()
        data = json.dumps(config).encode()
        self._additional_config_encrypted = cipher.encrypt(data)

    def get_additional_config(self) -> dict:
        if not self._additional_config_encrypted:
            return {}
        cipher = self._get_cipher()
        try:
            data = cipher.decrypt(bytes(self._additional_config_encrypted))
            return json.loads(data.decode())
        except Exception:
            return {}

    # Helper properties
    @property
    def has_credentials(self) -> bool:
        """Check if basic credentials are configured."""
        return bool(self._client_id_encrypted and self._client_secret_encrypted)

    @property
    def is_token_expired(self) -> bool:
        """Check if OAuth token is expired."""
        if not self.token_expiry:
            return True
        return timezone.now() >= self.token_expiry

    @property
    def requires_browser_oauth(self) -> bool:
        """Check if this provider needs browser redirect for OAuth."""
        # Providers that require browser OAuth for admin consent
        browser_required = {"google", "microsoft", "apple"}
        return self.provider in browser_required

    @property
    def callback_url(self) -> str:
        """Get OAuth callback URL for this provider."""
        try:
            site = Site.objects.get_current()
            protocol = "https" if not settings.DEBUG else "http"
            return (
                f"{protocol}://{site.domain}/accounts/{self.provider}/login/callback/"
            )
        except Exception:
            return f"/accounts/{self.provider}/login/callback/"

    @property
    def provider_console_url(self) -> str:
        """Get developer console URL for this provider."""
        console_urls = {
            "google": "https://console.cloud.google.com/apis/credentials",
            "facebook": "https://developers.facebook.com/apps/",
            "microsoft": "https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
            "github": "https://github.com/settings/developers",
            "apple": "https://developer.apple.com/account/resources/identifiers/list/serviceId",
            "twitter": "https://developer.twitter.com/en/portal/projects",
            "linkedin": "https://www.linkedin.com/developers/apps",
            "discord": "https://discord.com/developers/applications",
            "slack": "https://api.slack.com/apps",
        }
        return console_urls.get(self.provider, "")

    @classmethod
    def get_default_scopes(cls, provider: str) -> list:
        """Get default OAuth scopes for a provider."""
        default_scopes = {
            "google": ["openid", "email", "profile"],
            "facebook": ["email", "public_profile"],
            "microsoft": ["openid", "email", "profile", "User.Read"],
            "github": ["read:user", "user:email"],
            "apple": ["email", "name"],
            "twitter": ["users.read", "tweet.read"],
            "linkedin": ["r_liteprofile", "r_emailaddress"],
            "discord": ["identify", "email"],
            "slack": ["openid", "email", "profile"],
        }
        return default_scopes.get(provider, ["email", "profile"])

    def sync_to_allauth(self) -> bool:
        """
        Sync this configuration to allauth's SocialApp model.
        Creates or updates the corresponding SocialApp entry.
        """
        try:
            from allauth.socialaccount.models import SocialApp

            client_id = self.get_client_id()
            client_secret = self.get_client_secret()

            if not client_id or not client_secret:
                return False

            social_app, _created = SocialApp.objects.update_or_create(
                provider=self.provider,
                defaults={
                    "name": self.display_name or self.get_provider_display(),  # type: ignore[attr-defined]
                    "client_id": client_id,
                    "secret": client_secret,
                    "settings": self.settings_json or {},
                },
            )

            # Ensure current site is linked
            current_site = Site.objects.get_current()
            if current_site not in social_app.sites.all():
                social_app.sites.add(current_site)

            return True
        except Exception as e:
            self.last_error = str(e)
            self.status = "error"
            self.save(update_fields=["last_error", "status", "updated_at"])
            return False

    def test_connection(self) -> tuple[bool, str]:
        """
        Test if the provider credentials are valid.
        Returns (success, message).
        """
        try:
            client_id = self.get_client_id()
            client_secret = self.get_client_secret()

            if not client_id:
                return False, "Client ID is not configured"

            if not client_secret:
                return False, "Client Secret is not configured"

            # Provider-specific validation
            if self.provider == "google":
                # Google OAuth - check if client ID has correct format
                if not client_id.endswith(".apps.googleusercontent.com"):
                    return (
                        False,
                        "Google Client ID should end with .apps.googleusercontent.com",
                    )

            elif self.provider == "microsoft":
                # Microsoft - check tenant ID
                if not self.tenant_id:
                    # Default to 'common' if not specified
                    pass

            elif self.provider == "apple":
                if not self.team_id or not self.key_id:
                    return False, "Apple Sign In requires Team ID and Key ID"

            # Update test timestamp
            self.last_tested_at = timezone.now()
            self.last_error = ""
            self.save(update_fields=["last_tested_at", "last_error", "updated_at"])

            return True, "Credentials look valid. Test a real login to verify."

        except Exception as e:
            self.last_error = str(e)
            self.save(update_fields=["last_error", "updated_at"])
            return False, str(e)

    def update_user_count(self):
        """Update the count of users who signed up via this provider."""
        try:
            from allauth.socialaccount.models import SocialAccount

            self.users_count = SocialAccount.objects.filter(
                provider=self.provider
            ).count()
            self.save(update_fields=["users_count", "updated_at"])
        except Exception:  # noqa: S110
            pass


class SocialPostingAccount(models.Model):
    """
    Social media accounts for auto-posting blogs and firmware updates.

    Supports multiple platforms with different authentication methods:
    - OAuth2 flow (Facebook, Twitter, LinkedIn)
    - API Token/Bot Token (Telegram, Discord)
    - WhatsApp Business API

    Each platform can post to different destinations:
    - Pages, Groups, Channels, Profiles
    """

    PLATFORM_CHOICES = [
        ("facebook", "Facebook (Page/Group)"),
        ("twitter", "Twitter / X"),
        ("linkedin", "LinkedIn (Company/Personal)"),
        ("telegram", "Telegram (Channel/Group)"),
        ("whatsapp", "WhatsApp Business"),
        ("instagram", "Instagram (via Facebook)"),
        ("discord", "Discord (Webhook)"),
        ("reddit", "Reddit"),
        ("pinterest", "Pinterest"),
        ("tumblr", "Tumblr"),
        ("medium", "Medium"),
        ("threads", "Threads"),
    ]

    AUTH_TYPE_CHOICES = [
        ("oauth2", "OAuth 2.0 (Browser Login)"),
        ("api_token", "API Token / Bot Token"),
        ("webhook", "Webhook URL"),
        ("api_key_secret", "API Key + Secret"),
        ("access_token", "Access Token (Long-lived)"),
    ]

    DESTINATION_TYPE_CHOICES = [
        ("page", "Page"),
        ("group", "Group"),
        ("channel", "Channel"),
        ("profile", "Personal Profile"),
        ("company", "Company Page"),
        ("subreddit", "Subreddit"),
        ("board", "Board"),
        ("blog", "Blog"),
    ]

    STATUS_CHOICES = [
        ("unconfigured", "Not Configured"),
        ("pending_oauth", "Pending OAuth Authorization"),
        ("active", "Active"),
        ("token_expired", "Token Expired - Re-auth Required"),
        ("error", "Configuration Error"),
        ("disabled", "Disabled"),
    ]

    # Auto-posting content types
    POST_CONTENT_CHOICES = [
        ("blogs", "Blog Posts"),
        ("firmwares", "Firmware Uploads"),
        ("both", "Both Blogs & Firmwares"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform = models.CharField(max_length=32, choices=PLATFORM_CHOICES)
    account_name = models.CharField(
        max_length=255, help_text="Account/Page/Channel name"
    )
    auth_type = models.CharField(
        max_length=32, choices=AUTH_TYPE_CHOICES, default="oauth2"
    )

    # Destination details
    destination_type = models.CharField(
        max_length=32, choices=DESTINATION_TYPE_CHOICES, default="page"
    )
    destination_id = models.CharField(
        max_length=255, blank=True, help_text="Page ID, Channel ID, Group ID, etc."
    )
    destination_name = models.CharField(
        max_length=255, blank=True, help_text="Human-readable destination name"
    )
    destination_url = models.URLField(
        blank=True, help_text="URL to the page/channel/group"
    )

    # Encrypted credentials - different fields for different auth types
    _api_key_encrypted = models.BinaryField(
        blank=True, null=True, db_column="api_key_encrypted"
    )
    _api_secret_encrypted = models.BinaryField(
        blank=True, null=True, db_column="api_secret_encrypted"
    )
    _access_token_encrypted = models.BinaryField(
        blank=True, null=True, db_column="access_token_encrypted"
    )
    _refresh_token_encrypted = models.BinaryField(
        blank=True, null=True, db_column="refresh_token_encrypted"
    )
    _webhook_url_encrypted = models.BinaryField(
        blank=True, null=True, db_column="webhook_url_encrypted"
    )
    _bot_token_encrypted = models.BinaryField(
        blank=True, null=True, db_column="bot_token_encrypted"
    )

    # Additional config (JSON) - for platform-specific settings
    _additional_config_encrypted = models.BinaryField(
        blank=True, null=True, db_column="additional_config_encrypted"
    )

    # Token management
    token_expiry = models.DateTimeField(null=True, blank=True)
    token_scope = models.CharField(
        max_length=512, blank=True, help_text="OAuth scopes granted"
    )

    # Account metadata (fetched after OAuth)
    account_id = models.CharField(max_length=255, blank=True)
    account_username = models.CharField(max_length=255, blank=True)
    account_avatar_url = models.URLField(blank=True)
    followers_count = models.IntegerField(default=0)

    # Status
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default="unconfigured"
    )
    is_enabled = models.BooleanField(default=True)

    # Auto-posting configuration
    auto_post_enabled = models.BooleanField(
        default=True, help_text="Auto-post new content"
    )
    post_content_types = models.CharField(
        max_length=32, choices=POST_CONTENT_CHOICES, default="both"
    )
    post_template = models.TextField(
        blank=True,
        help_text="Custom post template with placeholders: {title}, {excerpt}, {url}, {tags}",
    )
    include_image = models.BooleanField(
        default=True, help_text="Include featured image in posts"
    )
    include_link = models.BooleanField(
        default=True, help_text="Include link to full content"
    )
    hashtags = models.CharField(
        max_length=512, blank=True, help_text="Default hashtags to add"
    )

    # Rate limiting
    min_post_interval_minutes = models.IntegerField(
        default=60, help_text="Minimum minutes between posts"
    )
    last_post_at = models.DateTimeField(null=True, blank=True)
    posts_today = models.IntegerField(default=0)
    daily_post_limit = models.IntegerField(
        default=10, help_text="Maximum posts per day"
    )

    # Stats
    total_posts = models.IntegerField(default=0)
    successful_posts = models.IntegerField(default=0)
    failed_posts = models.IntegerField(default=0)

    # Error tracking
    last_tested_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    consecutive_failures = models.IntegerField(default=0)

    # Notes
    notes = models.TextField(blank=True, default="")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_social_posting_accounts",
    )

    class Meta:
        ordering = ["platform", "account_name"]
        verbose_name = "Social Posting Account"
        verbose_name_plural = "Social Posting Accounts"
        # Allow multiple accounts per platform (e.g., multiple Facebook pages)
        unique_together = []

    def __str__(self):
        return f"{self.get_platform_display()} - {self.account_name} ({self.status})"  # type: ignore[attr-defined]

    def _get_cipher(self):
        return Fernet(get_encryption_key())

    # Encrypted field accessors
    def set_api_key(self, value: str):
        if not value:
            self._api_key_encrypted = None
            return
        cipher = self._get_cipher()
        self._api_key_encrypted = cipher.encrypt(value.encode())

    def get_api_key(self) -> str:
        if not self._api_key_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._api_key_encrypted)).decode()
        except Exception:
            return ""

    def set_api_secret(self, value: str):
        if not value:
            self._api_secret_encrypted = None
            return
        cipher = self._get_cipher()
        self._api_secret_encrypted = cipher.encrypt(value.encode())

    def get_api_secret(self) -> str:
        if not self._api_secret_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._api_secret_encrypted)).decode()
        except Exception:
            return ""

    def set_access_token(self, value: str):
        if not value:
            self._access_token_encrypted = None
            return
        cipher = self._get_cipher()
        self._access_token_encrypted = cipher.encrypt(value.encode())

    def get_access_token(self) -> str:
        if not self._access_token_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._access_token_encrypted)).decode()
        except Exception:
            return ""

    def set_refresh_token(self, value: str):
        if not value:
            self._refresh_token_encrypted = None
            return
        cipher = self._get_cipher()
        self._refresh_token_encrypted = cipher.encrypt(value.encode())

    def get_refresh_token(self) -> str:
        if not self._refresh_token_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._refresh_token_encrypted)).decode()
        except Exception:
            return ""

    def set_webhook_url(self, value: str):
        if not value:
            self._webhook_url_encrypted = None
            return
        cipher = self._get_cipher()
        self._webhook_url_encrypted = cipher.encrypt(value.encode())

    def get_webhook_url(self) -> str:
        if not self._webhook_url_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._webhook_url_encrypted)).decode()
        except Exception:
            return ""

    def set_bot_token(self, value: str):
        if not value:
            self._bot_token_encrypted = None
            return
        cipher = self._get_cipher()
        self._bot_token_encrypted = cipher.encrypt(value.encode())

    def get_bot_token(self) -> str:
        if not self._bot_token_encrypted:
            return ""
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(bytes(self._bot_token_encrypted)).decode()
        except Exception:
            return ""

    def set_additional_config(self, config: dict):
        if not config:
            self._additional_config_encrypted = None
            return
        cipher = self._get_cipher()
        data = json.dumps(config).encode()
        self._additional_config_encrypted = cipher.encrypt(data)

    def get_additional_config(self) -> dict:
        if not self._additional_config_encrypted:
            return {}
        cipher = self._get_cipher()
        try:
            data = cipher.decrypt(bytes(self._additional_config_encrypted))
            return json.loads(data.decode())
        except Exception:
            return {}

    @property
    def has_credentials(self) -> bool:
        """Check if any credentials are configured."""
        return any(
            [
                self._access_token_encrypted,
                self._api_key_encrypted,
                self._webhook_url_encrypted,
                self._bot_token_encrypted,
            ]
        )

    @property
    def is_token_expired(self) -> bool:
        """Check if OAuth token is expired."""
        if not self.token_expiry:
            return False  # No expiry set, assume valid
        return timezone.now() >= self.token_expiry

    @property
    def requires_oauth(self) -> bool:
        """Check if this platform requires OAuth browser flow."""
        oauth_platforms = {
            "facebook",
            "twitter",
            "linkedin",
            "instagram",
            "pinterest",
            "reddit",
            "threads",
        }
        return self.platform in oauth_platforms

    @property
    def can_post_now(self) -> bool:
        """Check if we can post right now (rate limiting)."""
        if not self.is_enabled or self.status != "active":
            return False
        if self.posts_today >= self.daily_post_limit:
            return False
        if self.last_post_at:
            minutes_since_last = (
                timezone.now() - self.last_post_at
            ).total_seconds() / 60
            if minutes_since_last < self.min_post_interval_minutes:
                return False
        return True

    @classmethod
    def get_auth_info(cls, platform: str) -> dict:
        """Get authentication requirements for a platform."""
        auth_info = {
            "facebook": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://www.facebook.com/v18.0/dialog/oauth",
                "scopes": [
                    "pages_manage_posts",
                    "pages_read_engagement",
                    "publish_to_groups",
                ],
                "setup_url": "https://developers.facebook.com/apps/",
                "description": "Connect to Facebook to post to Pages and Groups. Requires a Facebook App.",
                "fields_needed": ["app_id", "app_secret"],
            },
            "twitter": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://twitter.com/i/oauth2/authorize",
                "scopes": ["tweet.read", "tweet.write", "users.read"],
                "setup_url": "https://developer.twitter.com/en/portal/projects",
                "description": "Connect to Twitter/X to post tweets. Requires Twitter Developer Account.",
                "fields_needed": ["api_key", "api_secret"],
            },
            "linkedin": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://www.linkedin.com/oauth/v2/authorization",
                "scopes": ["w_member_social", "r_liteprofile"],
                "setup_url": "https://www.linkedin.com/developers/apps",
                "description": "Connect to LinkedIn to post to personal or company pages.",
                "fields_needed": ["client_id", "client_secret"],
            },
            "telegram": {
                "auth_type": "api_token",
                "requires_oauth": False,
                "setup_url": "https://t.me/BotFather",
                "description": "Create a Telegram Bot and add it to your channel/group as admin.",
                "fields_needed": ["bot_token", "chat_id"],
            },
            "whatsapp": {
                "auth_type": "api_token",
                "requires_oauth": False,
                "setup_url": "https://business.facebook.com/wa/manage/",
                "description": "Connect WhatsApp Business API for channel posts.",
                "fields_needed": ["phone_number_id", "access_token"],
            },
            "instagram": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://api.instagram.com/oauth/authorize",
                "scopes": ["instagram_basic", "instagram_content_publish"],
                "setup_url": "https://developers.facebook.com/apps/",
                "description": "Post to Instagram via Facebook Business. Requires Facebook App.",
                "fields_needed": ["app_id", "app_secret"],
            },
            "discord": {
                "auth_type": "webhook",
                "requires_oauth": False,
                "setup_url": "https://discord.com/developers/docs/resources/webhook",
                "description": "Create a Webhook in your Discord server channel settings.",
                "fields_needed": ["webhook_url"],
            },
            "reddit": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://www.reddit.com/api/v1/authorize",
                "scopes": ["submit", "read"],
                "setup_url": "https://www.reddit.com/prefs/apps",
                "description": "Post to subreddits. Requires Reddit App credentials.",
                "fields_needed": ["client_id", "client_secret"],
            },
            "pinterest": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://api.pinterest.com/oauth/",
                "scopes": ["boards:read", "pins:read", "pins:write"],
                "setup_url": "https://developers.pinterest.com/apps/",
                "description": "Pin content to Pinterest boards.",
                "fields_needed": ["app_id", "app_secret"],
            },
            "tumblr": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://www.tumblr.com/oauth2/authorize",
                "scopes": ["basic", "write"],
                "setup_url": "https://www.tumblr.com/oauth/apps",
                "description": "Post to Tumblr blogs.",
                "fields_needed": ["consumer_key", "consumer_secret"],
            },
            "medium": {
                "auth_type": "access_token",
                "requires_oauth": False,
                "setup_url": "https://medium.com/me/settings/security",
                "description": "Publish articles to Medium. Get an integration token from settings.",
                "fields_needed": ["integration_token"],
            },
            "threads": {
                "auth_type": "oauth2",
                "requires_oauth": True,
                "oauth_url": "https://threads.net/oauth/authorize",
                "scopes": ["threads_basic", "threads_content_publish"],
                "setup_url": "https://developers.facebook.com/apps/",
                "description": "Post to Threads (via Meta). Requires Facebook App.",
                "fields_needed": ["app_id", "app_secret"],
            },
        }
        return auth_info.get(platform, {})

    @property
    def auth_info(self) -> dict:
        """Get auth info for this account's platform."""
        return self.get_auth_info(self.platform)

    @property
    def oauth_callback_url(self) -> str:
        """Get OAuth callback URL for this platform."""
        try:
            from django.contrib.sites.models import Site

            site = Site.objects.get_current()
            protocol = "https" if not settings.DEBUG else "http"
            return f"{protocol}://{site.domain}/admin/distribution/social-posting/oauth/callback/{self.platform}/"
        except Exception:
            return f"/admin/distribution/social-posting/oauth/callback/{self.platform}/"

    def get_default_template(self) -> str:
        """Get default post template for this platform."""
        templates = {
            "twitter": "📢 {title}\n\n{excerpt}\n\n🔗 {url}\n\n{tags}",
            "facebook": "🆕 {title}\n\n{excerpt}\n\n👉 Read more: {url}\n\n{tags}",
            "linkedin": "📣 {title}\n\n{excerpt}\n\nRead the full article: {url}\n\n{tags}",
            "telegram": '📢 <b>{title}</b>\n\n{excerpt}\n\n🔗 <a href="{url}">Read More</a>\n\n{tags}',
            "discord": "**{title}**\n\n{excerpt}\n\n🔗 {url}",
            "whatsapp": "🆕 *{title}*\n\n{excerpt}\n\n🔗 {url}",
            "instagram": "{title}\n\n{excerpt}\n\n{tags}",
            "reddit": "{title}",  # Reddit uses title separately
            "pinterest": "{title} - {excerpt}",
            "medium": "{title}",  # Medium uses full article
            "tumblr": "# {title}\n\n{excerpt}\n\n[Read more]({url})\n\n{tags}",
            "threads": "📢 {title}\n\n{excerpt}\n\n🔗 {url}",
        }
        return templates.get(self.platform, "{title}\n\n{excerpt}\n\n{url}")

    def format_post(
        self,
        title: str,
        excerpt: str,
        url: str,
        tags: list | None = None,
        image_url: str | None = None,
    ) -> dict:
        """Format a post for this platform."""
        template = self.post_template or self.get_default_template()

        # Combine platform hashtags with content tags
        all_tags = []
        if tags:
            all_tags.extend([f"#{tag.replace(' ', '')}" for tag in tags])
        if self.hashtags:
            all_tags.extend([t.strip() for t in self.hashtags.split(",") if t.strip()])

        tags_str = " ".join(all_tags)

        content = template.format(
            title=title,
            excerpt=excerpt[:280] if self.platform == "twitter" else excerpt,
            url=url if self.include_link else "",
            tags=tags_str,
        )

        result = {
            "content": content.strip(),
            "platform": self.platform,
        }

        if self.include_image and image_url:
            result["image_url"] = image_url

        if self.platform == "reddit":
            result["title"] = title
            result["subreddit"] = self.destination_id

        return result

    def test_connection(self) -> tuple[bool, str]:
        """Test if the account credentials are valid."""
        try:
            if not self.has_credentials:
                return False, "No credentials configured"

            if self.is_token_expired:
                return False, "Access token has expired. Please re-authenticate."

            # Platform-specific validation
            if self.platform == "telegram":
                bot_token = self.get_bot_token()
                if not bot_token:
                    return False, "Bot token is required"
                if not self.destination_id:
                    return False, "Chat/Channel ID is required"
                # Could do actual API test here

            elif self.platform == "discord":
                webhook_url = self.get_webhook_url()
                if not webhook_url:
                    return False, "Webhook URL is required"
                if "discord.com/api/webhooks/" not in webhook_url:
                    return False, "Invalid Discord webhook URL"

            elif self.platform in ["facebook", "twitter", "linkedin", "instagram"]:
                access_token = self.get_access_token()
                if not access_token:
                    return (
                        False,
                        "Access token is required. Please complete OAuth flow.",
                    )

            self.last_tested_at = timezone.now()
            self.last_error = ""
            self.save(update_fields=["last_tested_at", "last_error", "updated_at"])

            return True, "Credentials look valid. Send a test post to verify."

        except Exception as e:
            self.last_error = str(e)
            self.save(update_fields=["last_error", "updated_at"])
            return False, str(e)

    def record_post(self, success: bool, error: str = ""):
        """Record a post attempt."""
        self.total_posts += 1
        if success:
            self.successful_posts += 1
            self.consecutive_failures = 0
            self.last_post_at = timezone.now()
            self.posts_today += 1
        else:
            self.failed_posts += 1
            self.consecutive_failures += 1
            self.last_error = error

            # Auto-disable after too many failures
            if self.consecutive_failures >= 5:
                self.status = "error"
                self.is_enabled = False

        self.save()

    def reset_daily_counter(self):
        """Reset daily post counter (call this daily via cron/celery)."""
        self.posts_today = 0
        self.save(update_fields=["posts_today", "updated_at"])
