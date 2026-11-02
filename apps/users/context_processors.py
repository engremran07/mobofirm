"""
Lightweight user context processors.
"""


def auth_status(request):
    """
    Inject a stable auth flag used by templates/JS toggles.
    """
    try:
        is_auth = bool(getattr(request, "user", None) and request.user.is_authenticated)
    except Exception:
        is_auth = False
    return {"auth_is_authenticated": is_auth}


# Backward compatibility for any legacy usage
def user_context(request):
    return {"is_authenticated": request.user.is_authenticated}
