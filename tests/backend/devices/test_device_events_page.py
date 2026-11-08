"""The staff device-events page renders inside the admin shell and enforces the permission."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.devices.models import DeviceEvent

pytestmark = pytest.mark.django_db


@pytest.fixture
def seed_events(user):
    DeviceEvent.objects.create(user=user, event_type="login", reason="Welcome back")
    DeviceEvent.objects.create(
        user=user, event_type="slot_reset", success=False, reason="Cap reached"
    )


@pytest.mark.usefixtures("seed_events")
def test_staff_see_the_events_in_the_admin_shell(staff):
    client = Client()
    client.force_login(staff)

    response = client.get(reverse("devices:device_events"))
    body = response.content.decode()

    assert response.status_code == 200
    assert "admin/staff_page.html" in [t.name for t in response.templates]
    assert 'class="mf-ui-title"' in body
    assert b"unfold/css/styles.css" in response.content
    assert "Welcome back" in body and "Cap reached" in body
    assert "mf-ui-badge--danger" in body
    assert 'class="surface-card' not in body
    assert "messages" not in body.split('class="mf-ui"')[1]


@pytest.mark.usefixtures("seed_events")
def test_the_type_filter_narrows_the_list(staff):
    client = Client()
    client.force_login(staff)

    body = client.get(reverse("devices:device_events") + "?type=login").content.decode()

    assert "Welcome back" in body
    assert "Cap reached" not in body


@pytest.mark.usefixtures("seed_events")
def test_an_unknown_type_filter_is_ignored(staff):
    client = Client()
    client.force_login(staff)

    body = client.get(reverse("devices:device_events") + "?type=<x>").content.decode()

    assert "Welcome back" in body and "<x>" not in body


@pytest.mark.usefixtures("seed_events")
def test_staff_without_the_view_permission_are_denied(make_user):
    clerk = make_user("clerk")
    clerk.is_staff = True
    clerk.save()
    client = Client()
    client.force_login(clerk)

    assert client.get(reverse("devices:device_events")).status_code == 403

    clerk.user_permissions.add(Permission.objects.get(codename="view_deviceevent"))
    client = Client()
    client.force_login(clerk)
    assert client.get(reverse("devices:device_events")).status_code == 200


def test_regular_users_and_anonymous_visitors_are_sent_to_the_admin_login(user):
    anonymous = Client().get(reverse("devices:device_events"))
    client = Client()
    client.force_login(user)
    regular = client.get(reverse("devices:device_events"))

    assert anonymous.status_code == regular.status_code == 302
    assert regular["Location"].startswith("/admin/login/")
