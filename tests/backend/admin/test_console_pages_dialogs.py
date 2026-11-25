"""Staff dialogs on the console: GET renders into #console-dialog and never writes; POST validates."""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.tags.models import Tag

HTMX = {"HX-Request": "true"}


def _staff(username: str, *, permission: str = "") -> Client:
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )
    if permission:
        user.user_permissions.add(Permission.objects.get(codename=permission))
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture(name="suggestion")
def suggestion_fixture() -> Tag:
    return Tag.objects.create(name="Console suggestion", is_suggested=True)


@pytest.mark.django_db
def test_get_renders_the_dialog_fragment_and_never_changes_data(suggestion):
    client = _staff("dialog-curator", permission="change_tag")

    response = client.get(
        reverse("tags:reject_dialog", args=[suggestion.pk]), headers=HTMX
    )

    html = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in html and "<aside" not in html
    assert "data-console-dialog" in html and "btn-danger" in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert "hx-post=" in html
    assert "HX-Request" in response["Vary"]
    suggestion.refresh_from_db()
    assert suggestion.is_suggested and suggestion.is_active


@pytest.mark.django_db
def test_get_without_javascript_is_the_same_form_inside_the_console_page(suggestion):
    client = _staff("dialog-curator-plain", permission="change_tag")

    response = client.get(reverse("tags:reject_dialog", args=[suggestion.pk]))

    html = response.content.decode()
    assert response.status_code == 200
    assert "<aside" in html and "console-staff-page" in html
    assert "hx-post=" not in html and 'method="post"' in html
    assert "console-staff-dialog card" in html


@pytest.mark.django_db
def test_post_closes_the_dialog_and_applies_the_change(suggestion):
    client = _staff("dialog-curator-post", permission="change_tag")

    response = client.post(
        reverse("tags:reject_dialog", args=[suggestion.pk]), headers=HTMX
    )

    assert response.status_code == 204
    assert "dialog:close" in json.loads(response["HX-Trigger"])
    suggestion.refresh_from_db()
    assert not suggestion.is_suggested and not suggestion.is_active


@pytest.mark.django_db
def test_plain_post_redirects_after_the_change(suggestion):
    client = _staff("dialog-curator-prg", permission="change_tag")

    response = client.post(reverse("tags:reject_dialog", args=[suggestion.pk]))

    assert response.status_code == 302
    assert response["Location"] == reverse("tags:manage")


@pytest.mark.django_db
def test_staff_without_the_permission_cannot_open_or_submit(suggestion):
    client = _staff("dialog-bare")
    url = reverse("tags:reject_dialog", args=[suggestion.pk])

    assert client.get(url, headers=HTMX).status_code == 403
    assert client.post(url, headers=HTMX).status_code == 403
    suggestion.refresh_from_db()
    assert suggestion.is_suggested and suggestion.is_active


@pytest.mark.django_db
def test_post_needs_a_csrf_token(suggestion):
    client = Client(enforce_csrf_checks=True)
    user = get_user_model().objects.create_user(
        username="dialog-csrf",
        email="dialog-csrf@example.test",
        is_staff=True,
        email_verified_at=timezone.now(),
    )
    user.user_permissions.add(Permission.objects.get(codename="change_tag"))
    client.force_login(user)

    response = client.post(
        reverse("tags:reject_dialog", args=[suggestion.pk]), headers=HTMX
    )

    assert response.status_code == 403
    suggestion.refresh_from_db()
    assert suggestion.is_suggested
