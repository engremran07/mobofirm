"""Permissions-Policy and Cross-Origin-Resource-Policy response headers.

Django's ``SecurityMiddleware`` already sends nosniff, Referrer-Policy and
Cross-Origin-Opener-Policy from settings, and the CSP middleware sends the strict
Content-Security-Policy; neither has a setting for these two headers. They are applied
here, from ``AdminContentSecurityPolicyMiddleware`` (the core security middleware that
is already installed), so no CSP behaviour changes.

* ``Permissions-Policy`` disables powerful browser features the site never uses
  (camera, microphone, geolocation, payment, USB and motion sensors). Features
  that embedded players rely on (fullscreen, picture-in-picture, autoplay,
  encrypted-media) are deliberately left at the browser default.
* ``Cross-Origin-Resource-Policy: same-site`` stops other sites from embedding our
  responses as no-cors subresources. CORS requests are unaffected.

Both can be overridden in settings (``PERMISSIONS_POLICY`` mapping of feature to a
list of allowlist entries, ``CROSS_ORIGIN_RESOURCE_POLICY`` string; an empty value
turns the header off) and a view that sets either header itself keeps its value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from django.conf import settings
from django.http import HttpResponseBase

from apps.core.runtime_config import VALID_CROSS_ORIGIN_RESOURCE_POLICIES

PERMISSIONS_POLICY_HEADER = "Permissions-Policy"
CROSS_ORIGIN_RESOURCE_POLICY_HEADER = "Cross-Origin-Resource-Policy"

DEFAULT_PERMISSIONS_POLICY: Mapping[str, Sequence[str]] = {
    "accelerometer": (),
    "camera": (),
    "geolocation": (),
    "gyroscope": (),
    "magnetometer": (),
    "microphone": (),
    "payment": (),
    "usb": (),
}
DEFAULT_CROSS_ORIGIN_RESOURCE_POLICY = "same-site"


def _allowlist(entries: Sequence[str]) -> str:
    # ``self`` is a keyword; every other entry is a quoted origin.
    items = (entry if entry == "self" else f'"{entry}"' for entry in entries)
    return "(" + " ".join(items) + ")"


def permissions_policy_value(policy: Mapping[str, Sequence[str]]) -> str:
    """Serialise ``{"camera": [], "fullscreen": ["self"]}`` as a header value."""
    return ", ".join(
        f"{feature}={_allowlist(entries)}" for feature, entries in policy.items()
    )


def apply_security_headers(response: HttpResponseBase) -> HttpResponseBase:
    """Add Permissions-Policy and CORP unless the response already carries them."""
    policy = getattr(settings, "PERMISSIONS_POLICY", DEFAULT_PERMISSIONS_POLICY)
    if policy and PERMISSIONS_POLICY_HEADER not in response:
        response[PERMISSIONS_POLICY_HEADER] = permissions_policy_value(policy)

    corp = getattr(
        settings, "CROSS_ORIGIN_RESOURCE_POLICY", DEFAULT_CROSS_ORIGIN_RESOURCE_POLICY
    )
    if (
        corp in VALID_CROSS_ORIGIN_RESOURCE_POLICIES
        and CROSS_ORIGIN_RESOURCE_POLICY_HEADER not in response
    ):
        response[CROSS_ORIGIN_RESOURCE_POLICY_HEADER] = corp
    return response
