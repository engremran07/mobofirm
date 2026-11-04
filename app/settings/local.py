import os

from .base import *  # noqa: F403

DEBUG = True
SECURE_SSL_REDIRECT = False
MAILERS = {  # noqa: F405
    **MAILERS,  # noqa: F405
    "default": {
        **MAILERS["default"],  # noqa: F405
        "BACKEND": os.getenv(
            "EMAIL_BACKEND",
            (
                "apps.core.email_backends.gmail.GmailBackend"
                if GMAIL_ENABLED  # noqa: F405
                else "django.core.mail.backends.console.EmailBackend"
            ),
        ),
    },
}
DATABASES["default"]["OPTIONS"] = {  # noqa: F405
    **DATABASES["default"].get("OPTIONS", {}),  # noqa: F405
    "options": "-c search_path=mobofirm_local",
}
