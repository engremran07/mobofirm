import os
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

from . import base
from .security import PRODUCTION_ADMIN_CSP, PRODUCTION_CSP

# Re-export every uppercase setting from base so Django sees it on this module.
globals().update({name: value for name, value in vars(base).items() if name.isupper()})

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ImproperlyConfigured(message)


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


DEBUG = False

_require(
    len(base.SECRET_KEY) >= 50 and len(set(base.SECRET_KEY)) >= 5,
    "DJANGO_SECRET_KEY must be at least 50 characters of random data in production.",
)
_require(
    os.getenv("DB_PASSWORD") is not None,
    "DB_PASSWORD must be explicitly configured in production.",
)
_require(
    any(host not in _LOCAL_HOSTS and host != "*" for host in base.ALLOWED_HOSTS)
    and "*" not in base.ALLOWED_HOSTS,
    "DJANGO_ALLOWED_HOSTS must list the production hostname(s), without '*'.",
)

_site_url = urlsplit(base.SITE_URL)
_require(
    _site_url.scheme == "https" and bool(_site_url.netloc),
    "SITE_URL must be the public https:// origin of the site in production.",
)
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "DJANGO_CSRF_TRUSTED_ORIGINS", f"{_site_url.scheme}://{_site_url.netloc}"
    ).split(",")
    if origin.strip()
]
_require(
    all(origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS),
    "DJANGO_CSRF_TRUSTED_ORIGINS must contain https:// origins only.",
)

_require(
    bool(base.MFA_ENCRYPTION_KEY) and base.MFA_ENCRYPTION_KEY != base.SECRET_KEY,
    "MFA_ENCRYPTION_KEY must be configured separately from DJANGO_SECRET_KEY.",
)
try:
    Fernet(base.SOCIAL_CREDENTIAL_ENCRYPTION_KEY)
except (ValueError, TypeError) as exc:
    raise ImproperlyConfigured(
        "SOCIAL_CREDENTIAL_ENCRYPTION_KEY must be a Fernet key "
        "(32 url-safe base64-encoded bytes)."
    ) from exc

_require(
    bool(os.getenv("CACHE_URL")),
    "CACHE_URL (a shared Redis cache) is required so rate limits hold across workers.",
)
_require(
    bool(os.getenv("CELERY_BROKER_URL")),
    "CELERY_BROKER_URL must be configured explicitly in production.",
)

_require(
    not base.GMAIL_ENABLED or bool(base.GMAIL_USERNAME and base.GMAIL_APP_PASSWORD),
    "GMAIL_ENABLED requires GMAIL_USERNAME and GMAIL_APP_PASSWORD.",
)
_default_mailer = base.MAILERS["default"]
_require(
    _default_mailer["BACKEND"]
    not in {
        "django.core.mail.backends.console.EmailBackend",
        "django.core.mail.backends.locmem.EmailBackend",
        "django.core.mail.backends.dummy.EmailBackend",
    },
    "Production requires a real email backend (SMTP or Gmail), not console/locmem/dummy.",
)
_require(
    _default_mailer["BACKEND"] != "django.core.mail.backends.smtp.EmailBackend"
    or bool(_default_mailer["OPTIONS"]["host"]),
    "EMAIL_HOST is required when the SMTP email backend is selected.",
)
# The runtime backend prefers the admin-edited email settings but still needs a
# working environment configuration to fall back on (SMTP host or Gmail).
_require(
    _default_mailer["BACKEND"] != base.RUNTIME_EMAIL_BACKEND
    or bool(_default_mailer["OPTIONS"]["host"])
    or base.GMAIL_ENABLED,
    "EMAIL_HOST (or GMAIL_ENABLED with credentials) is required for the runtime "
    "email backend.",
)
_require(
    bool(base.DEFAULT_FROM_EMAIL)
    and not base.DEFAULT_FROM_EMAIL.endswith("@localhost"),
    "DEFAULT_FROM_EMAIL must be configured with a production sender address.",
)

# Behind the nginx reverse proxy by default; override only for a different topology.
TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))
NUM_PROXIES = TRUSTED_PROXY_COUNT
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT
FORCE_HTTPS = True
QUEUE_BACKEND = os.getenv("QUEUE_BACKEND", "celery")
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = True
# Orchestrator probes reach the app over plain HTTP inside the network.
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = _env_flag("USE_X_FORWARDED_HOST")
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_CSP = PRODUCTION_CSP
SECURE_CSP_ADMIN = PRODUCTION_ADMIN_CSP
