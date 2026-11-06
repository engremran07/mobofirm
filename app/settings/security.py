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

# Policy for the Unfold shell (the admin and the staff pages that extend it; see
# apps.core.middleware.admin_csp; the public site keeps BASE_CSP). Unfold's Alpine build compiles x-data/x-on expressions with
# `new Function`, which needs 'unsafe-eval' for scripts; every script stays
# same-origin. Inline <style> blocks carry the per-request nonce, and Alpine's
# style bindings need inline style *attributes* (style-src-attr), which cannot run
# script. No ad or third-party hosts are allowed.
ADMIN_CSP: dict[str, list[str]] = {
    **BASE_CSP,
    "connect-src": [CSP.SELF],
    "frame-src": [CSP.NONE],
    "img-src": [CSP.SELF, "data:"],
    "script-src": [CSP.SELF, CSP.NONCE, CSP.UNSAFE_EVAL],
    "style-src": [CSP.SELF, CSP.NONCE],
    "style-src-attr": [CSP.UNSAFE_INLINE],
}

PRODUCTION_ADMIN_CSP: dict[str, list[str]] = {
    **ADMIN_CSP,
    "upgrade-insecure-requests": [],
}
