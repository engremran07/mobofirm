import os
from importlib import import_module

# Tests must not depend on a developer's .env; the key is test-only.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key-not-for-deployment")

base = import_module("app.settings.base")

globals().update({name: value for name, value in vars(base).items() if name.isupper()})

DEBUG = False
MFA_ENCRYPTION_KEY = "test-only-mfa-encryption-key"
# A valid Fernet key (32 bytes, url-safe base64) used only by the test suite.
SOCIAL_CREDENTIAL_ENCRYPTION_KEY = "dGVzdC1vbmx5LXNvY2lhbC1jcmVkZW50aWFsLWtleSE="
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "mobofirm-tests",
    }
}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
TRUSTED_PROXY_COUNT = 0
TRUSTED_PROXIES: frozenset[str] = frozenset()
NUM_PROXIES = 0
ALLAUTH_TRUSTED_PROXY_COUNT = 0
COUNTRY_HEADER = ""
SIGNUP_ATTEMPTS_PER_HOUR = 100000
# Scoped auth throttles stay strict; the generic limits must not trip on the suite.
REST_FRAMEWORK = {
    **base.REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {
        **base.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],
        "anon": "100000/min",
        "user": "100000/min",
    },
}
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
