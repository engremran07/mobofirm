"""Every icon named by the console shell, navigation and staff pages exists in the bundled Lucide set.

The console bundles a tree-shaken registry (``frontend/src/admin/js/icons.js``); a name that is not
registered renders as an empty box, so the names used server-side are compared with it here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.core import admin_nav_catalog as catalog
from apps.core.admin_actions import ICON_ALIASES

ROOT = Path(__file__).resolve().parents[3]
ICON_SOURCE = (ROOT / "frontend" / "src" / "admin" / "js" / "icons.js").read_text(
    encoding="utf-8"
)
TEMPLATE_ICON = re.compile(
    r'(?:console_icon\s+"|\bicon="|data-lucide=")([a-z][a-z0-9-]*)"'
)
RENDERED_ICON = re.compile(r'data-lucide="([a-z][a-z0-9-]*)"')
STAFF_LAYOUTS = ('extends "admin/', 'extends "console/')


def _kebab(pascal: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Z])", "-", pascal).lower()


def _registered() -> set[str]:
    block = ICON_SOURCE.split("} from", 1)[0].split("import {", 1)[1]
    block = re.sub(r"\b(\w+)\s+as\s+\w+", r"\1", block)
    return {_kebab(name) for name in re.findall(r"\b([A-Z][A-Za-z0-9]*)\b", block)}


def _console_templates() -> list[Path]:
    directory = Path(django_settings.BASE_DIR) / "templates"
    found = []
    for path in directory.rglob("*.html"):
        relative = path.relative_to(directory).parts
        text = path.read_text(encoding="utf-8")
        if relative[0] in {"admin", "console"} or any(
            marker in text for marker in STAFF_LAYOUTS
        ):
            found.append(path)
    return found


def test_registry_parser_finds_the_bundled_icons():
    registered = _registered()

    assert {"house", "chevron-right", "layout-dashboard", "rows-3"} <= registered


def test_navigation_catalogue_icons_are_registered():
    names = {
        catalog.OVERVIEW_ICON,
        catalog.OTHER_GROUP_ICON,
        catalog.OTHER_ITEM_ICON,
    }
    for group in catalog.NAV_GROUPS:
        names.add(group.icon)
        names.update(entry.icon for entry in group.entries)

    assert sorted(names - _registered()) == []


def test_action_icon_aliases_point_at_registered_icons():
    assert sorted(set(ICON_ALIASES.values()) - _registered()) == []


def test_template_icons_are_registered():
    registered = _registered()
    missing: dict[str, list[str]] = {}
    for path in _console_templates():
        names = set(TEMPLATE_ICON.findall(path.read_text(encoding="utf-8")))
        unknown = sorted(names - registered)
        if unknown:
            missing[path.name] = unknown

    assert missing == {}


@pytest.mark.django_db
def test_rendered_shell_and_dashboard_icons_are_registered(settings):
    settings.STAFF_MFA_REQUIRED = False
    user = get_user_model().objects.create_superuser(
        username="icon-owner", email="icon-owner@example.test"
    )
    client = Client()
    client.force_login(user)
    urls = [
        reverse("admin:index"),
        reverse("admin:app_list", kwargs={"app_label": "blog"}),
        *(
            reverse("admin:console_dashboard_widget", args=[slug])
            for slug in ("overview", "attention", "activity", "health")
        ),
    ]

    used: set[str] = set()
    for url in urls:
        response = client.get(url, HTTP_HX_REQUEST="true")
        assert response.status_code == 200, url
        used.update(RENDERED_ICON.findall(response.content.decode()))

    assert sorted(used - _registered()) == []
