from .base import *  # noqa: F403

DEBUG = False
MFA_ENCRYPTION_KEY = "test-only-mfa-encryption-key"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {  # noqa: F405
    **MAILERS,  # noqa: F405
    "default": {
        **MAILERS["default"],  # noqa: F405
        "BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    },
}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
