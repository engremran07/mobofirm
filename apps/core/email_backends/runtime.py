"""Email backend driven by the runtime site settings.

``SiteSettings.email_mode`` picks the transport:

* ``gmail`` / ``smtp`` - the credentials stored (encrypted) in the admin;
* ``environment`` - the deployment's own configuration: Gmail from ``GMAIL_*`` when
  enabled, else SMTP when the mailer ``OPTIONS`` name a host, else
  ``DEFAULT_EMAIL_BACKEND`` (console in development).

Select it with ``MAILERS["default"]["BACKEND"]`` and put the environment SMTP values
in ``OPTIONS``; pinning ``EMAIL_BACKEND`` to another class bypasses it entirely.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.backends.smtp import EmailBackend as SmtpEmailBackend
from django.utils.module_loading import import_string

from apps.site_settings.services import EmailRuntimeConfig, get_email_runtime_config

logger = logging.getLogger(__name__)

GMAIL_HOST = "smtp.gmail.com"
GMAIL_PORT = 587
DEFAULT_ALIAS = "default"


class RuntimeEmailBackend(BaseEmailBackend):
    def __init__(
        self,
        *args: Any,
        host: str = "",
        port: int = 587,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout: int = 10,
        alias: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, alias=alias, **kwargs)
        self._alias = alias or DEFAULT_ALIAS
        self._environment_smtp: dict[str, Any] = {
            "host": host,
            "port": port,
            "username": username,
            "password": password,
            "use_tls": use_tls,
            "use_ssl": use_ssl,
            "timeout": timeout,
        }
        config = get_email_runtime_config()
        self._from_email = self._sender(config)
        self._backend = self._build_backend(config)

    @staticmethod
    def _sender(config: EmailRuntimeConfig) -> str:
        if config.mode == "gmail":
            return config.from_email or config.gmail_username
        return config.from_email if config.mode == "smtp" else ""

    def _smtp(self, **options: Any) -> BaseEmailBackend:
        return SmtpEmailBackend(alias=self._alias, **options)

    def _build_backend(self, config: EmailRuntimeConfig) -> BaseEmailBackend:
        if config.mode == "gmail":
            if not (config.gmail_username and config.gmail_app_password):
                raise ImproperlyConfigured(
                    "Gmail email mode needs a Gmail address and an app password."
                )
            return self._smtp(
                host=GMAIL_HOST,
                port=GMAIL_PORT,
                username=config.gmail_username,
                password=config.gmail_app_password,
                use_tls=True,
                use_ssl=False,
            )
        if config.mode == "smtp":
            if not config.smtp_host:
                raise ImproperlyConfigured("SMTP email mode needs a host.")
            return self._smtp(
                host=config.smtp_host,
                port=config.smtp_port,
                username=config.smtp_username,
                password=config.smtp_password,
                use_tls=config.smtp_use_tls,
                use_ssl=config.smtp_use_ssl,
            )
        return self._environment_backend()

    def _environment_backend(self) -> BaseEmailBackend:
        if getattr(settings, "GMAIL_ENABLED", False):
            username = str(getattr(settings, "GMAIL_USERNAME", "")).strip()
            app_password = str(getattr(settings, "GMAIL_APP_PASSWORD", "")).strip()
            if username and app_password:
                self._from_email = str(
                    getattr(settings, "GMAIL_FROM_EMAIL", "") or username
                )
                return self._smtp(
                    host=GMAIL_HOST,
                    port=GMAIL_PORT,
                    username=username,
                    password=app_password,
                    use_tls=True,
                    use_ssl=False,
                )
            logger.warning("GMAIL_ENABLED is set without GMAIL_USERNAME/PASSWORD.")
        if self._environment_smtp["host"]:
            return self._smtp(**self._environment_smtp)
        fallback_path = getattr(
            settings,
            "DEFAULT_EMAIL_BACKEND",
            "django.core.mail.backends.console.EmailBackend",
        )
        try:
            fallback_class = import_string(fallback_path)
        except ImportError as exc:
            raise ImproperlyConfigured(
                f"DEFAULT_EMAIL_BACKEND {fallback_path!r} cannot be imported: {exc}"
            ) from exc
        return fallback_class(alias=self._alias)

    def open(self) -> bool | None:
        return self._backend.open()

    def close(self) -> None:
        self._backend.close()

    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        if not email_messages:
            return 0
        if self._from_email:
            for message in email_messages:
                if message.from_email in {"", settings.DEFAULT_FROM_EMAIL}:
                    message.from_email = self._from_email
        return self._backend.send_messages(email_messages)
