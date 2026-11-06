from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


def _get_fallback_backend_class():
    backend_path = getattr(
        settings,
        "DEFAULT_EMAIL_BACKEND",
        "django.core.mail.backends.console.EmailBackend",
    )
    try:
        return import_string(backend_path)
    except ImportError as exc:
        raise ImproperlyConfigured(
            f"DEFAULT_EMAIL_BACKEND {backend_path!r} cannot be imported: {exc}"
        ) from exc


def _gmail_config() -> dict[str, Any]:
    if not getattr(settings, "GMAIL_ENABLED", False):
        return {}
    username = (getattr(settings, "GMAIL_USERNAME", "") or "").strip()
    app_password = (getattr(settings, "GMAIL_APP_PASSWORD", "") or "").strip()
    from_email = (getattr(settings, "GMAIL_FROM_EMAIL", "") or username).strip()
    if not username or not app_password:
        logger.warning("Gmail backend is enabled but credentials are not configured.")
        return {}
    return {
        "host": "smtp.gmail.com",
        "port": 587,
        "username": username,
        "password": app_password,
        "use_tls": True,
        "from_email": from_email,
    }


class GmailBackend(BaseEmailBackend):
    """
    Dynamic Gmail backend that reads credentials from Django settings.
    Falls back to DEFAULT_EMAIL_BACKEND when Gmail is disabled or misconfigured.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cfg = _gmail_config()
        if cfg:
            self._from_email = cfg.pop("from_email")
            self._backend = SmtpEmailBackend(**cfg)
        else:
            self._backend = _get_fallback_backend_class()()
            self._from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)

    def open(self):
        return self._backend.open()

    def close(self):
        return self._backend.close()

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        if self._from_email:
            for m in email_messages:
                if not m.from_email:
                    m.from_email = self._from_email
        return self._backend.send_messages(email_messages)
