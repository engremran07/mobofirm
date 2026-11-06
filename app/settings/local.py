import os

from . import base

globals().update({name: value for name, value in vars(base).items() if name.isupper()})

DEBUG = True
SECURE_SSL_REDIRECT = False
# Local development crawls its own http://127.0.0.1 server; production never does.
SEO_FETCH_ALLOW_PRIVATE_NETWORKS = True
MAILERS = {
    "default": {
        "BACKEND": os.getenv("EMAIL_BACKEND", base.RUNTIME_EMAIL_BACKEND),
    },
}
DATABASES = {
    "default": {
        **base.DATABASES["default"],
        "OPTIONS": {
            **base.DATABASES["default"].get("OPTIONS", {}),
            "options": f"-c search_path={os.getenv('DB_SCHEMA', 'mobofirm_local')}",
        },
    }
}
