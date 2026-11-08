from __future__ import annotations

from apps.devices.services import attach_device_cookie, ensure_browser_key


class DeviceBrowserKeyCookieMiddleware:
    """Issue a random, HttpOnly browser-profile key without collecting traits."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ensure_browser_key(request)
        response = self.get_response(request)
        if getattr(request, "device_browser_key_needs_cookie", False):
            attach_device_cookie(response, request=request)
        return response
