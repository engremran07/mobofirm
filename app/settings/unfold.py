"""Unfold configuration (single admin theme source).

Branding colours are anchored to the public design tokens in
``frontend/src/styles/tokens.css`` (light accent ``#0b7376``, dark accent
``#72d9ce``); ``tests/backend/admin/test_admin_theme.py`` fails if the two drift.
Callables are dotted paths so nothing here imports app code at settings-load time.
"""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _

BRAND_PRIMARY_LIGHT = "#0b7376"
BRAND_PRIMARY_STRONG_LIGHT = "#07595d"
BRAND_PRIMARY_DARK = "#72d9ce"
BRAND_PRIMARY_STRONG_DARK = "#4fc4b7"

# Unfold buttons use primary-600 (light) / primary-500 (dark) with white text, so
# the mid steps stay dark enough for contrast while 300/400 carry the mint accent
# used for text and focus rings on dark surfaces.
_PRIMARY = {
    "50": "#effaf9",
    "100": "#d5f3f0",
    "200": "#a8e6e0",
    "300": BRAND_PRIMARY_DARK,
    "400": BRAND_PRIMARY_STRONG_DARK,
    "500": "#0c7a7d",
    "600": BRAND_PRIMARY_LIGHT,
    "700": BRAND_PRIMARY_STRONG_LIGHT,
    "800": "#06484b",
    "900": "#05393c",
    "950": "#032628",
}

# Neutral scale: warm paper tones in light mode, the blue-slate surfaces of the
# public dark theme in dark mode (page #151c24, surface #1d2730, soft #293740).
_BASE = {
    "50": "#f5f2eb",
    "100": "#ece7de",
    "200": "#d9d2c7",
    "300": "#adbbb8",
    "400": "#8a9a9d",
    "500": "#52636a",
    "600": "#3d4d54",
    "700": "#293740",
    "800": "#1d2730",
    "900": "#151c24",
    "950": "#0f151b",
}

UNFOLD: dict[str, Any] = {
    "SITE_TITLE": "apps.core.admin_branding.site_title",
    "SITE_HEADER": "apps.core.admin_branding.site_header",
    "SITE_SUBHEADER": "apps.core.admin_branding.site_subheader",
    "SITE_VERSION": "apps.core.admin_branding.site_version",
    "SITE_URL": "apps.core.admin_branding.site_url",
    "SITE_SYMBOL": "memory",
    "SITE_ICON": "apps.core.admin_branding.site_icon",
    "SITE_FAVICONS": "apps.core.admin_branding.favicons",
    "SITE_DROPDOWN": [
        {
            "title": _("Public site"),
            "icon": "public",
            "link": "apps.core.admin_branding.site_url",
        },
        {
            "title": _("Health check"),
            "icon": "monitor_heart",
            "link": "apps.core.admin_branding.health_url",
        },
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "SHOW_BACK_BUTTON": False,
    "ENVIRONMENT": "apps.core.admin_branding.environment_label",
    "BORDER_RADIUS": "6px",
    "COLORS": {"base": _BASE, "primary": _PRIMARY},
    "STYLES": [],
    "SCRIPTS": [],
    "DASHBOARD_CALLBACK": "apps.core.admin_dashboard.dashboard_callback",
    "ACCOUNT": {
        "navigation": "apps.core.admin_navigation.account_links",
    },
    "COMMAND": {
        "search_models": [
            "users.customuser",
            "firmwares.brand",
            "firmwares.model",
            "firmwares.variant",
            "firmwares.chipset",
            "firmwares.pendingfirmware",
            "firmwares.officialfirmware",
            "blog.post",
            "tags.tag",
            "comments.comment",
            "pages.page",
            "shop.order",
            "marketplace.listing",
            "filemanager.fileitem",
        ],
        "show_history": True,
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": "apps.core.admin_navigation.build_sidebar",
    },
    "TABS": "apps.core.admin_navigation.build_tabs",
}
