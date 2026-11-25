"""Shared security policy values for configured Django environments."""

import os
import re

from django.utils.csp import CSP

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

# Ad-network hosts allowed to serve scripts, frames, images and beacons. Empty by
# default so no third-party script can run unless the operator opts in.
CSP_AD_HOSTS = [
    f"https://{host}"
    for host in (
        value.strip().lower() for value in os.getenv("CSP_AD_HOSTS", "").split(",")
    )
    if host and _HOSTNAME_RE.fullmatch(host)
]

BASE_CSP: dict[str, list[str]] = {
    "default-src": [CSP.SELF],
    "base-uri": [CSP.SELF],
    "connect-src": [CSP.SELF, *CSP_AD_HOSTS],
    "font-src": [CSP.SELF],
    # The social-login POST redirects to Google's consent screen; browsers apply
    # form-action to that redirect, so only that one origin is allowed besides self.
    "form-action": [CSP.SELF, "https://accounts.google.com"],
    "frame-ancestors": [CSP.NONE],
    "frame-src": [*CSP_AD_HOSTS] or [CSP.NONE],
    "img-src": [CSP.SELF, "data:", *CSP_AD_HOSTS],
    "object-src": [CSP.NONE],
    "script-src": [CSP.SELF, *CSP_AD_HOSTS],
    # This fixed Pygments stylesheet is emitted by DRF's browsable API renderer.
    "style-src": [
        CSP.SELF,
        "'sha256-1kn+agGaGZdaFPNbx228buXQB9KXcy9RVE6NMUiW4SQ='",
    ],
}

PRODUCTION_CSP: dict[str, list[str]] = {
    **BASE_CSP,
    "upgrade-insecure-requests": [],
}

# Policy for the staff console (the admin and the staff pages that share its shell; see
# apps.core.middleware.admin_csp; the public site keeps BASE_CSP). The console ships
# one same-origin script bundle (htmx + Alpine's CSP build, no expression evaluation)
# and has no inline scripts or event handlers, so scripts need neither 'unsafe-eval'
# nor 'unsafe-inline' nor a nonce. Styles are same-origin files; only <style> elements
# carrying the per-request nonce are additionally allowed (no style *attributes*: the
# components toggle classes and CSSOM properties, which CSP does not restrict). No ad
# or third-party hosts are allowed, and forms may only post back to this site.
ADMIN_CSP: dict[str, list[str]] = {
    **BASE_CSP,
    "connect-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-src": [CSP.NONE],
    "img-src": [CSP.SELF, "data:"],
    "script-src": [CSP.SELF],
    "style-src": [CSP.SELF, CSP.NONCE],
}

PRODUCTION_ADMIN_CSP: dict[str, list[str]] = {
    **ADMIN_CSP,
    "upgrade-insecure-requests": [],
}
