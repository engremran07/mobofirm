import hashlib

from django.http import HttpRequest


def _get_client_ip(request: HttpRequest) -> str:
    """
    Normalized client IP resolution (aligns with RequestMetaMiddleware).
    Prefer the first X-Forwarded-For value when present.
    """
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "") or ""
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]
    return request.META.get("REMOTE_ADDR", "") or ""


def get_device_fingerprint(request: HttpRequest) -> str:
    """
    Stable device fingerprint based on user agent, client IP and session key.

    Prefers client-provided fingerprint (from JS cookie/localStorage) so it
    remains stable across HTTP/HTTPS and pre-auth flows. Falls back to a
    server-derived hash when none is provided.
    """
    # Prefer explicit client fingerprint (from hidden form field or cookie)
    client_fp = ""
    try:
        client_fp = (
            request.POST.get("device_fp")
            or request.COOKIES.get("device_fp")
            or request.GET.get("device_fp", "")
        )
    except Exception:
        client_fp = ""

    if client_fp:
        return str(client_fp)

    # Server-side fallback (ensures a session key exists)
    ua = request.META.get("HTTP_USER_AGENT", "") or ""
    ip = _get_client_ip(request)
    session = getattr(request, "session", None)
    session_key = ""
    try:
        if session is not None:
            # Create a session key if it doesn't exist yet (pre-auth)
            if not session.session_key:
                session.save()
            session_key = session.session_key or ""
    except Exception:
        session_key = ""

    payload = f"{ua}|{ip}|{session_key}"
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()
