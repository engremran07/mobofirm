"""
Lightweight user context processors.
"""

from django.http import HttpRequest
from django.utils.functional import SimpleLazyObject

from apps.users.services.announcements import banner_announcements


def announcement_banners(request: HttpRequest) -> dict[str, object]:
    """Live site banners for this visitor; evaluated only if a template reads them."""
    return {
        "announcement_banners": SimpleLazyObject(lambda: banner_announcements(request))
    }


def auth_status(request):
    """
    Inject a stable auth flag used by templates/JS toggles.
    """
    user = getattr(request, "user", None)
    return {"auth_is_authenticated": bool(user and user.is_authenticated)}


# Backward compatibility for any legacy usage
def user_context(request):
    return {"is_authenticated": request.user.is_authenticated}
