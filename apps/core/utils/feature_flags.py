from django.conf import settings


def seo_enabled() -> bool:
    return bool(getattr(settings, "SEO_ENABLED", False))
