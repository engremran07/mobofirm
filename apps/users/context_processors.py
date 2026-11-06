"""
Lightweight user context processors.
"""


def auth_status(request):
    """
    Inject a stable auth flag used by templates/JS toggles.
    """
    user = getattr(request, "user", None)
    return {"auth_is_authenticated": bool(user and user.is_authenticated)}


# Backward compatibility for any legacy usage
def user_context(request):
    return {"is_authenticated": request.user.is_authenticated}
