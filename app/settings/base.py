import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from . import unfold as unfold_config
from .security import ADMIN_CSP, BASE_CSP

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

UNFOLD = unfold_config.UNFOLD

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be configured in the environment or .env."
    )

DEBUG = False
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    # Unfold must precede the admin app so its templates win. BasicAppConfig keeps
    # Unfold from swapping admin.site; MobofirmAdminConfig installs the project site.
    "unfold.apps.BasicAppConfig",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "apps.core.admin_config.MobofirmAdminConfig",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django.contrib.sitemaps",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "rest_framework",
    "rest_framework.authtoken",
    "solo",
    "apps.users.apps.UsersConfig",
    "apps.core.apps.CoreConfig",
    "apps.ads.apps.AdsConfig",
    "apps.devices.apps.DevicesConfig",
    "apps.blog.apps.BlogConfig",
    "apps.comments.apps.CommentsConfig",
    "apps.firmwares.apps.FirmwaresConfig",
    "apps.tags.apps.TagsConfig",
    "apps.storage.apps.StorageConfig",
    "apps.seo.apps.SeoConfig",
    "apps.pages.apps.PagesConfig",
    "apps.shop.apps.ShopConfig",
    "apps.marketplace.apps.MarketplaceConfig",
    "apps.wallet.apps.WalletConfig",
    "apps.referral.apps.ReferralConfig",
    "apps.consent.apps.ConsentConfig",
    "apps.gamification.apps.GamificationConfig",
    "apps.bounty.apps.BountyConfig",
    "apps.distribution.apps.DistributionConfig",
    "apps.promotions.apps.PromotionsConfig",
    "apps.site_settings.apps.SiteSettingsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "apps.core.middleware.admin_csp.AdminContentSecurityPolicyMiddleware",
    "apps.site_settings.middleware.SiteAnalyticsCspMiddleware",
    "apps.core.middleware.correlation.CorrelationIdMiddleware",
    "apps.core.middleware.app_version.AppVersionHeaderMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.site_settings.middleware.SiteLocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.maintenance.MaintenanceModeMiddleware",
    "apps.users.middleware.reset_throttle.PasswordResetThrottleMiddleware",
    "apps.referral.middleware.ReferralCaptureMiddleware",
    "apps.devices.middleware.DeviceBrowserKeyCookieMiddleware",
    "apps.shop.middleware.EntitlementCacheMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.users.middleware.mfa_enforce.EnforceMfaMiddleware",
    "apps.users.middleware.profile_completion.EnforceProfileCompletionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.seo.middleware.RedirectFallbackMiddleware",
    "apps.seo.middleware.RobotsHeaderMiddleware",
]

SECURE_CSP = BASE_CSP
SECURE_CSP_ADMIN = ADMIN_CSP

ROOT_URLCONF = "app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.csp",
                "app.context_processors.release_metadata",
                "apps.site_settings.context_processors.site_settings",
                "app.context_processors.site_chrome",
                "apps.users.context_processors.announcement_banners",
            ],
        },
    },
]

WSGI_APPLICATION = "app.wsgi.application"
ASGI_APPLICATION = "app.asgi.application"

DATABASES: dict[str, dict[str, Any]] = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "mobofirm"),
        "USER": os.getenv("DB_USER", "mobofirm_app"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
FIRMWARE_STORAGE_ROOT = BASE_DIR / "storage"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_NAME = os.getenv("SITE_NAME", "Mobofirm")
SITE_DESCRIPTION = os.getenv("SITE_DESCRIPTION", "")
SITE_URL = os.getenv("SITE_URL", "")
# Published on the seeded Contact and DMCA pages; the .invalid default is a dummy
# address that `seed_pages` warns about until a real mailbox is configured.
SITE_CONTACT_EMAIL = os.getenv("SITE_CONTACT_EMAIL", "contact@mobofirm.invalid")
# SEO outbound HTTP (SafeFetcher). Private networks stay blocked unless local
# settings opt in; credentials for optional integrations come from the environment.
SEO_FETCH_ALLOW_PRIVATE_NETWORKS = False
SEO_PAGESPEED_API_KEY = os.getenv("SEO_PAGESPEED_API_KEY", "")
SEO_GSC_ACCESS_TOKEN = os.getenv("SEO_GSC_ACCESS_TOKEN", "")
SEO_GSC_PROPERTY = os.getenv("SEO_GSC_PROPERTY", "")
ENABLE_SIGNUP = os.getenv("ENABLE_SIGNUP", "true").lower() in {"1", "true", "yes"}
REQUIRE_MFA = os.getenv("REQUIRE_MFA", "false").lower() in {"1", "true", "yes"}
REQUIRE_PROFILE_COMPLETION = os.getenv(
    "REQUIRE_PROFILE_COMPLETION", "false"
).lower() in {"1", "true", "yes"}
MAINTENANCE_MODE = os.getenv("MAINTENANCE_MODE", "false").lower() in {
    "1",
    "true",
    "yes",
}
BLOG_ENABLED = os.getenv("BLOG_ENABLED", "false").lower() in {"1", "true", "yes"}
BLOG_COMMENTS_ENABLED = os.getenv("BLOG_COMMENTS_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
BLOG_TRANSLATION_LANGUAGES = [
    code.strip()
    for code in os.getenv("BLOG_TRANSLATION_LANGUAGES", "").split(",")
    if code.strip()
]
ALLOW_USER_BLOG_POSTS = os.getenv("ALLOW_USER_BLOG_POSTS", "false").lower() in {
    "1",
    "true",
    "yes",
}
PRIMARY_COLOR = os.getenv("PRIMARY_COLOR", "#0d6efd")
SECONDARY_COLOR = os.getenv("SECONDARY_COLOR", "#6c757d")
SITE_LOGO_URL = os.getenv("SITE_LOGO_URL", "")
SITE_DARK_LOGO_URL = os.getenv("SITE_DARK_LOGO_URL", "")
SITE_FAVICON_URL = os.getenv("SITE_FAVICON_URL", "")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY", "")
# Consent app (apps.consent): first-party cookie holding the visitor's choice, and
# how long the anonymised audit records are kept.
CONSENT_COOKIE_NAME = os.getenv("CONSENT_COOKIE_NAME", "mf_consent")
CONSENT_COOKIE_MAX_AGE = int(os.getenv("CONSENT_COOKIE_MAX_AGE", str(365 * 24 * 3600)))
CONSENT_RECORD_RETENTION_DAYS = int(os.getenv("CONSENT_RECORD_RETENTION_DAYS", "1095"))
DEVICE_MAX_PENDING_PER_USER = int(os.getenv("DEVICE_MAX_PENDING_PER_USER", "10"))
DEVICE_EVENT_RETENTION_DAYS = int(os.getenv("DEVICE_EVENT_RETENTION_DAYS", "90"))
API_TOKEN_TTL_SECONDS = int(os.getenv("API_TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))
SIGNUP_ATTEMPTS_PER_HOUR = int(os.getenv("SIGNUP_ATTEMPTS_PER_HOUR", "10"))
# Number of trusted reverse proxies in front of the app (nginx = 1). Zero means
# REMOTE_ADDR is used and forwarded headers are ignored.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "0"))
TRUSTED_PROXIES = frozenset(
    proxy.strip() for proxy in os.getenv("TRUSTED_PROXIES", "").split(",") if proxy
)
NUM_PROXIES = TRUSTED_PROXY_COUNT
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT
# Edge header carrying the visitor's ISO country (for example CF-IPCountry).
COUNTRY_HEADER = os.getenv("COUNTRY_HEADER", "")
MFA_ENCRYPTION_KEY = os.getenv("MFA_ENCRYPTION_KEY", "")
SOCIAL_CREDENTIAL_ENCRYPTION_KEY = os.getenv("SOCIAL_CREDENTIAL_ENCRYPTION_KEY", "")
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() in {"1", "true", "yes"}
MFA_TOTP_ISSUER = os.getenv("MFA_TOTP_ISSUER", "Account Portal")
GMAIL_ENABLED = os.getenv("GMAIL_ENABLED", "false").lower() in {"1", "true", "yes"}
GMAIL_USERNAME = os.getenv("GMAIL_USERNAME", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
GMAIL_FROM_EMAIL = os.getenv("GMAIL_FROM_EMAIL", "")
FIRMWARE_DOWNLOAD_GATE_ENABLED = os.getenv(
    "FIRMWARE_DOWNLOAD_GATE_ENABLED", "true"
).lower() in {"1", "true", "yes"}
FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS = int(
    os.getenv("FIRMWARE_DOWNLOAD_COUNTDOWN_SECONDS", "10")
)
FIRMWARE_DOWNLOAD_AD_GATE_ENABLED = os.getenv(
    "FIRMWARE_DOWNLOAD_AD_GATE_ENABLED", "false"
).lower() in {"1", "true", "yes"}
FIRMWARE_DOWNLOAD_AD_GATE_SECONDS = int(
    os.getenv("FIRMWARE_DOWNLOAD_AD_GATE_SECONDS", "30")
)
FIRMWARE_DOWNLOAD_TOKEN_EXPIRY_MINUTES = int(
    os.getenv("FIRMWARE_DOWNLOAD_TOKEN_EXPIRY_MINUTES", "30")
)
FIRMWARE_DOWNLOAD_REQUIRE_LOGIN = os.getenv(
    "FIRMWARE_DOWNLOAD_REQUIRE_LOGIN", "false"
).lower() in {"1", "true", "yes"}
FIRMWARE_DOWNLOAD_MAX_PER_DAY = int(os.getenv("FIRMWARE_DOWNLOAD_MAX_PER_DAY", "0"))
FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION = os.getenv(
    "FIRMWARE_DOWNLOAD_HOTLINK_PROTECTION", "true"
).lower() in {"1", "true", "yes"}
FIRMWARE_DOWNLOAD_LINK_ENCRYPTION = os.getenv(
    "FIRMWARE_DOWNLOAD_LINK_ENCRYPTION", "true"
).lower() in {"1", "true", "yes"}
FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS = int(
    os.getenv("FIRMWARE_DOWNLOAD_LINK_TTL_SECONDS", "600")
)
# Hosts (exact, or "*.example.com" for subdomains) that external firmware URLs may use.
FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS = [
    host.strip().lower()
    for host in os.getenv(
        "FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS", "downloads.example.invalid"
    ).split(",")
    if host.strip()
]
# Dedicated non-login account that authors automated brand/model/chipset posts.
FIRMWARE_BLOG_AUTOMATION_USERNAME = os.getenv(
    "FIRMWARE_BLOG_AUTOMATION_USERNAME", "mobofirm-automation"
)
AUTH_USER_MODEL = "users.CustomUser"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
SITE_ID = 1
ACCOUNT_LOGIN_METHODS = {"email", "username"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_ADAPTER = "apps.users.adapters.CustomAccountAdapter"
SOCIALACCOUNT_ADAPTER = "apps.users.adapters.CustomSocialAccountAdapter"
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
SOCIALACCOUNT_EMAIL_VERIFICATION = "mandatory"
LOGIN_REDIRECT_URL = "/users/dashboard/"
CSRF_FAILURE_VIEW = "app.views.csrf_failure"
_google_client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
_google_client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
if bool(_google_client_id) != bool(_google_client_secret):
    raise RuntimeError(
        "GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be configured together."
    )
SOCIALACCOUNT_PROVIDERS = {}
if _google_client_id and _google_client_secret:
    SOCIALACCOUNT_PROVIDERS["google"] = {
        "APP": {
            "client_id": _google_client_id,
            "secret": _google_client_secret,
            "key": "",
        },
        "SCOPE": ["openid", "email", "profile"],
        "AUTH_PARAMS": {"prompt": "select_account"},
        "OAUTH_PKCE_ENABLED": True,
    }
LOGIN_URL = "/users/login/"
DEFAULT_EMAIL_BACKEND = os.getenv(
    "DEFAULT_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
# The runtime backend reads the admin-edited email settings (site_settings) and
# falls back to the environment values below; pinning EMAIL_BACKEND bypasses it.
RUNTIME_EMAIL_BACKEND = "apps.core.email_backends.runtime.RuntimeEmailBackend"
_email_backend = os.getenv("EMAIL_BACKEND", RUNTIME_EMAIL_BACKEND)
_smtp_options = {
    "host": os.getenv("EMAIL_HOST", ""),
    "port": int(os.getenv("EMAIL_PORT", "587")),
    "username": os.getenv("EMAIL_HOST_USER", ""),
    "password": os.getenv("EMAIL_HOST_PASSWORD", ""),
    "use_tls": os.getenv("EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes"},
    "use_ssl": os.getenv("EMAIL_USE_SSL", "false").lower() in {"1", "true", "yes"},
    "timeout": int(os.getenv("EMAIL_TIMEOUT", "10")),
}
# Django 6.1 reads only BACKEND and OPTIONS; other backends reject SMTP options.
MAILERS: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": _email_backend,
        **(
            {"OPTIONS": _smtp_options}
            if _email_backend
            in {"django.core.mail.backends.smtp.EmailBackend", RUNTIME_EMAIL_BACKEND}
            else {}
        ),
    }
}
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", GMAIL_FROM_EMAIL or GMAIL_USERNAME or "noreply@localhost"
)
# A shared cache is required for rate limits and MFA throttles to hold across
# workers; LocMemCache is per process and only suitable for local development.
CACHE_URL = os.getenv("CACHE_URL", "")
if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
            "KEY_PREFIX": "mobofirm",
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mobofirm",
        }
    }

REST_FRAMEWORK = {
    # Token authentication is opt-in per view (see apps.users.api_authentication).
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "240/min",
        "auth_login": "10/min",
        "auth_signup": "5/hour",
        "auth_google": "10/min",
        "auth_password_reset": "5/hour",
    },
}

FIRMWARE_BLOG_AUTOGENERATION_ASYNC = os.getenv(
    "FIRMWARE_BLOG_AUTOGENERATION_ASYNC", "false"
).strip().lower() in {"1", "true", "yes"}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
QUEUE_BACKEND = os.getenv("QUEUE_BACKEND", "sync")
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULE = {
    "blog-publish-due-scheduled-posts": {
        "task": "apps.blog.tasks.publish_due_scheduled_posts",
        "schedule": 60,
    },
    "blog-process-queued-topics": {
        "task": "apps.blog.tasks.process_queued_topics",
        "schedule": 60 * 60,
    },
    "tags-curate-suggested-tags": {
        "task": "apps.tags.tasks.curate_suggested_tags",
        "schedule": 24 * 60 * 60,
    },
    "tags-refresh-usage-counts": {
        "task": "apps.tags.tasks.refresh_usage_counts",
        "schedule": 24 * 60 * 60,
    },
    "firmwares-sync-automated-blogs": {
        "task": "apps.firmwares.tasks.catchup_missing_blog_posts",
        "schedule": 6 * 60 * 60,
    },
    "firmwares-expire-download-tokens": {
        "task": "apps.firmwares.tasks.expire_download_tokens",
        "schedule": 60 * 60,
    },
    "firmwares-aggregate-daily-stats": {
        "task": "apps.firmwares.tasks.aggregate_daily_firmware_stats",
        "schedule": 24 * 60 * 60,
    },
    "firmwares-cleanup-tracking-data": {
        "task": "apps.firmwares.tasks.cleanup_old_tracking_data",
        "schedule": 7 * 24 * 60 * 60,
    },
    "storage-verify-locations": {
        "task": "apps.storage.tasks.verify_storage_locations",
        "schedule": 24 * 60 * 60,
    },
    "consent-purge-old-records": {
        "task": "apps.consent.tasks.purge_old_records",
        "schedule": 24 * 60 * 60,
    },
    "bounty-expire-due": {
        "task": "apps.bounty.tasks.expire_bounties",
        "schedule": 60 * 60,
    },
    "blog-reconcile-guide-fulfilments": {
        "task": "apps.blog.tasks.reconcile_guide_fulfilments",
        "schedule": 15 * 60,
    },
    "distribution-process-share-jobs": {
        "task": "apps.distribution.tasks.process_share_jobs",
        "schedule": 60,
    },
    "shop-expire-packages": {
        "task": "apps.shop.tasks.expire_packages",
        "schedule": 60 * 60,
    },
    "promotions-sync-statuses": {
        "task": "apps.promotions.tasks.sync_statuses",
        "schedule": 60,
    },
    "core-cleanup-old-data": {
        "task": "apps.core.tasks.cleanup_old_data",
        "schedule": 24 * 60 * 60,
    },
    "seo-process-pending-jobs": {
        "task": "apps.seo.tasks.process_pending_jobs",
        "schedule": 60,
    },
    "seo-scheduled-scan": {
        "task": "apps.seo.tasks.scheduled_scan",
        "schedule": 6 * 60 * 60,
    },
    "seo-scheduled-crawl": {
        "task": "apps.seo.tasks.scheduled_crawl",
        "schedule": 24 * 60 * 60,
    },
    "devices-purge-old-events": {
        "task": "apps.devices.tasks.purge_old_device_events",
        "schedule": 24 * 60 * 60,
    },
    "devices-expire-old-fingerprints": {
        "task": "apps.devices.tasks.expire_old_fingerprints",
        "schedule": 24 * 60 * 60,
    },
    "devices-cleanup-behavior-insights": {
        "task": "apps.devices.tasks.cleanup_behavior_insights",
        "schedule": 7 * 24 * 60 * 60,
    },
}

# Local filesystem storage is the default provider; cloud providers stay disabled
# until their packages are installed and the explicit flags below are enabled.
STORAGE_GOOGLE_DRIVE_ENABLED = os.getenv(
    "STORAGE_GOOGLE_DRIVE_ENABLED", "false"
).lower() in {"1", "true", "yes"}
STORAGE_S3_ENABLED = os.getenv("STORAGE_S3_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
# Free-tier allowance (new files per calendar day / month) for accounts without a
# package, used only while no active PackageTier is marked as the default tier;
# the admin-editable default tier always wins. -1 means unlimited.
SHOP_FREE_DAILY_DOWNLOADS = int(os.getenv("SHOP_FREE_DAILY_DOWNLOADS", "5"))
SHOP_FREE_MONTHLY_DOWNLOADS = int(os.getenv("SHOP_FREE_MONTHLY_DOWNLOADS", "50"))
SHOP_FREE_DEVICE_LIMIT = int(os.getenv("SHOP_FREE_DEVICE_LIMIT", "3"))
# Share of each paid-file sale (in %) the platform keeps before crediting the seller.
MARKETPLACE_COMMISSION_PERCENT = int(os.getenv("MARKETPLACE_COMMISSION_PERCENT", "0"))
# External download links use the firmware catalogue allow-list
# (FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS); storage does not keep a second one.

SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
RECAPTCHA_ENABLED = os.getenv("RECAPTCHA_ENABLED", "").lower() in {"1", "true", "yes"}
RECAPTCHA_MODE = os.getenv("RECAPTCHA_MODE", "off").lower()
RECAPTCHA_PRIVATE_KEY = os.getenv("RECAPTCHA_PRIVATE_KEY", "")
RECAPTCHA_TIMEOUT_MS = int(os.getenv("RECAPTCHA_TIMEOUT_MS", "3000"))
RECAPTCHA_SCORE_THRESHOLD = float(os.getenv("RECAPTCHA_SCORE_THRESHOLD", "0.5"))
RECAPTCHA_EXPECTED_HOSTNAME = os.getenv("RECAPTCHA_EXPECTED_HOSTNAME", "")

LOG_DIR = os.getenv("LOG_DIR", "")
LOG_RETENTION_DAYS = int(os.getenv("LOG_RETENTION_DAYS", "14"))
_log_handlers = ["console"]
_file_handlers = {}
if LOG_DIR:
    # Daily-rotated file with bounded retention; console output stays enabled so
    # container runtimes can still collect stdout.
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    _file_handlers["file"] = {
        "class": "logging.handlers.TimedRotatingFileHandler",
        "filename": str(Path(LOG_DIR) / "mobofirm.log"),
        "when": "midnight",
        "backupCount": LOG_RETENTION_DAYS,
        "encoding": "utf-8",
        "formatter": "standard",
    }
    _log_handlers.append("file")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
        **_file_handlers,
    },
    "root": {"handlers": _log_handlers, "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
}
