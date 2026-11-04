import os
from pathlib import Path

from dotenv import load_dotenv
from .security import BASE_CSP

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

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
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
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
    "apps.governance.apps.GovernanceConfig",
    "apps.blog.apps.BlogConfig",
    "apps.comments.apps.CommentsConfig",
    "apps.firmwares.apps.FirmwaresConfig",
    "apps.tags.apps.TagsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "apps.users.middleware.mfa_enforce.EnforceMfaMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

SECURE_CSP = BASE_CSP

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
                "app.context_processors.release_metadata",
            ],
        },
    },
]

WSGI_APPLICATION = "app.wsgi.application"
ASGI_APPLICATION = "app.asgi.application"

DATABASES = {
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
CONSENT_BANNER_ENABLED = os.getenv("CONSENT_BANNER_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
}
DEVICE_MAX_DEVICES_PER_USER = int(os.getenv("DEVICE_MAX_DEVICES_PER_USER", "3"))
DEVICE_FINGERPRINT_MODE = os.getenv("DEVICE_FINGERPRINT_MODE", "strict").lower()
DEVICE_QUOTA_ENFORCEMENT_ENABLED = os.getenv(
    "DEVICE_QUOTA_ENFORCEMENT_ENABLED", "false"
).lower() in {"1", "true", "yes"}
DEVICE_QUOTA_WINDOW_DAYS = int(os.getenv("DEVICE_QUOTA_WINDOW_DAYS", "365"))
DEVICE_QUOTA_DEFAULT_WINDOW = os.getenv("DEVICE_QUOTA_DEFAULT_WINDOW", "12m")
DEVICE_QUOTA_DEFAULT_LIMIT = int(os.getenv("DEVICE_QUOTA_DEFAULT_LIMIT", "5"))
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
_email_backend = os.getenv(
    "EMAIL_BACKEND",
    (
        "apps.core.email_backends.gmail.GmailBackend"
        if GMAIL_ENABLED
        else "django.core.mail.backends.smtp.EmailBackend"
    ),
)
MAILERS = {
    "default": {
        "BACKEND": _email_backend,
        "HOST": os.getenv("EMAIL_HOST", ""),
        "PORT": int(os.getenv("EMAIL_PORT", "587")),
        "USERNAME": os.getenv("EMAIL_HOST_USER", ""),
        "PASSWORD": os.getenv("EMAIL_HOST_PASSWORD", ""),
        "USE_TLS": os.getenv("EMAIL_USE_TLS", "true").lower() in {"1", "true", "yes"},
        "USE_SSL": os.getenv("EMAIL_USE_SSL", "false").lower() in {"1", "true", "yes"},
    }
}
DEFAULT_FROM_EMAIL = os.getenv(
    "DEFAULT_FROM_EMAIL", GMAIL_FROM_EMAIL or GMAIL_USERNAME or "noreply@localhost"
)
CACHE_BACKEND = "django.core.cache.backends.locmem.LocMemCache"
CACHES = {"default": {"BACKEND": CACHE_BACKEND, "LOCATION": "mobofirm"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_THROTTLE_RATES": {
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
CELERY_TASK_IGNORE_RESULT = True

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
        }
    },
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
}
