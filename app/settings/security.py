from django.utils.csp import CSP

BASE_CSP = {
    "default-src": [CSP.SELF],
    "base-uri": [CSP.SELF],
    "connect-src": [CSP.SELF],
    "font-src": [CSP.SELF],
    "form-action": [CSP.SELF],
    "frame-ancestors": [CSP.NONE],
    "img-src": [CSP.SELF, "data:"],
    "object-src": [CSP.NONE],
    "script-src": [CSP.SELF],
    # This fixed Pygments stylesheet is emitted by DRF's browsable API renderer.
    "style-src": [
        CSP.SELF,
        "'sha256-1kn+agGaGZdaFPNbx228buXQB9KXcy9RVE6NMUiW4SQ='",
    ],
}

PRODUCTION_CSP = {
    **BASE_CSP,
    "upgrade-insecure-requests": [],
}
