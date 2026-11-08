"""Consent categories the codebase relies on; the slugs are a stable contract."""

from __future__ import annotations

from typing import Final

NECESSARY: Final = "necessary"
ANALYTICS: Final = "analytics"
ADVERTISING: Final = "advertising"

# slug -> (name, description, required, sort order)
BUILTIN_CATEGORIES: Final[dict[str, tuple[str, str, bool, int]]] = {
    NECESSARY: (
        "Strictly necessary",
        (
            "Sign-in, security, CSRF protection, your language and theme, and the "
            "record of this choice. The site cannot work without them, so they are "
            "always on."
        ),
        True,
        10,
    ),
    ANALYTICS: (
        "Analytics",
        (
            "Google Analytics and Google Tag Manager, when the site operator has "
            "configured them. They help the operator see which pages are used."
        ),
        False,
        20,
    ),
    ADVERTISING: (
        "Advertising",
        (
            "Ad placement, rotation, impression and click measurement, and affiliate "
            "tracking. Without this choice no ads are measured or personalised."
        ),
        False,
        30,
    ),
}

SOURCE_BANNER: Final = "banner"
SOURCE_PRIVACY_CENTRE: Final = "privacy_centre"
SOURCE_ADMIN: Final = "admin"

ACTION_ACCEPT_ALL: Final = "accept_all"
ACTION_REJECT_ALL: Final = "reject_all"
ACTION_CUSTOM: Final = "custom"

# Cookies written by the optional trackers; they are expired when consent is
# withdrawn, because the tracker can no longer clean up after itself.
TRACKING_COOKIE_PREFIXES: Final = ("_ga", "_gid", "_gat", "_gcl_", "_dc_gtm_")
