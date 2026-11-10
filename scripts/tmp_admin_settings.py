"""Throwaway settings for the admin audit (deleted after use)."""

import app.settings.local as _local

globals().update({k: v for k, v in vars(_local).items() if k.isupper()})
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SECURE_SSL_REDIRECT = False
ALLOWED_HOSTS = ["*"]
