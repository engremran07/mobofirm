from django.conf import settings

DEFAULTS = {
    "VERIFICATION_CODE_TTL_SECONDS": 900,  # 15 minutes
    "VERIFICATION_CODE_RESEND_COOLDOWN": 60,  # 1 minute
    "VERIFICATION_EMAIL_SUBJECT": "Your verification code",
    "VERIFICATION_EMAIL_FROM": None,  # falls back to DEFAULT_FROM_EMAIL
}


def get(key: str, default=None):
    return getattr(settings, "USERS_CONFIG", {}).get(key, DEFAULTS.get(key, default))
