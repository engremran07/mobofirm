import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .security import PRODUCTION_CSP

DEBUG = False

if not os.getenv("DJANGO_SECRET_KEY"):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production.")
if os.getenv("DB_PASSWORD") is None:
    raise ImproperlyConfigured(
        "DB_PASSWORD must be explicitly configured in production."
    )
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must contain a production hostname."
    )
if GMAIL_ENABLED and not (GMAIL_USERNAME and GMAIL_APP_PASSWORD):  # noqa: F405
    raise ImproperlyConfigured(
        "GMAIL_ENABLED requires GMAIL_USERNAME and GMAIL_APP_PASSWORD."
    )
_default_mailer = MAILERS["default"]  # noqa: F405
if (
    _default_mailer["BACKEND"] == "django.core.mail.backends.smtp.EmailBackend"
    and not _default_mailer["HOST"]
):
    raise ImproperlyConfigured(
        "EMAIL_HOST is required when the SMTP email backend is selected."
    )
if not DEFAULT_FROM_EMAIL or DEFAULT_FROM_EMAIL.endswith("@localhost"):  # noqa: F405
    raise ImproperlyConfigured(
        "DEFAULT_FROM_EMAIL must be configured with a production sender address."
    )

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31_536_000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CSP = PRODUCTION_CSP
