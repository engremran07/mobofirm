"""A row-action dialog requested over HTMX is a content-only fragment (never the whole console shell)."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.tags.models import Tag

HX = {"HX-Request": "true", "HX-Target": "dialog#console-dialog"}


@pytest.fixture(name="staff_client")
def staff_client_fixture(client):
    user = get_user_model().objects.create_superuser(username="dlg", email="dlg@example.test")
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_htmx_action_dialog_is_a_fragment_inside_the_dialog_host(staff_client):
    tag = Tag.objects.create(name="Dialog tag", ai_suggested=True, is_curated=False)

    response = staff_client.get(f"/admin/tags/tag/{tag.pk}/approve/", headers=HX)
    html = response.content.decode()

    assert response.status_code == 200
    assert "data-console-dialog-content" in html and "modal-content" in html
    assert "<html" not in html and 'id="console-dialog"' not in html and "app-wrapper" not in html


@pytest.mark.django_db
def test_plain_action_dialog_request_is_a_full_page_with_the_same_form(staff_client):
    tag = Tag.objects.create(name="Dialog tag two", ai_suggested=True, is_curated=False)

    html = staff_client.get(f"/admin/tags/tag/{tag.pk}/approve/").content.decode()

    assert "<html" in html and 'method="post"' in html
    assert "data-console-dialog-content" not in html
