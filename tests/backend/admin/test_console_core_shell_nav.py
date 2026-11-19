"""The sidebar never links to a page that fails, and shows each person only what they may open."""

from __future__ import annotations

import re

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

SIDEBAR_LINK = re.compile(r'<a class="nav-link[^"]*" href="([^"]+)"')


@pytest.fixture(autouse=True)
def _console_without_staff_mfa(settings):
    # The staff MFA gate is covered under tests/backend/users; these tests exercise the sidebar.
    settings.STAFF_MFA_REQUIRED = False


def _sidebar_links(client: Client) -> list[str]:
    html = client.get(reverse("admin:index")).content.decode()
    return SIDEBAR_LINK.findall(html[html.index("<aside") : html.index("</aside>")])


@pytest.mark.django_db
def test_every_staff_tool_in_the_sidebar_opens_for_a_superuser():
    user = get_user_model().objects.create_superuser(
        username="nav-owner", email="nav-owner@example.test"
    )
    client = Client()
    client.force_login(user)

    links = _sidebar_links(client)
    tools = sorted({link for link in links if not link.startswith("/admin/")})

    assert reverse("admin:index") in links and tools
    for link in tools:
        response = client.get(link)
        assert response.status_code == 200, link


@pytest.mark.django_db
def test_staff_without_permissions_only_get_the_dashboard():
    user = get_user_model().objects.create_user(
        username="nav-bare",
        email="nav-bare@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )
    client = Client()
    client.force_login(user)

    links = _sidebar_links(client)

    # The readiness probe is open to every signed-in staff member, so it is the only extra link.
    assert set(links) - {reverse("admin:index"), reverse("health-ready")} == set()
