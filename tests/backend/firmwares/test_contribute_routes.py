"""Contribute a firmware: the canonical address and the legacy upload redirects."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture(name="member_client")
def _member_client(django_user_model: Any) -> Client:
    user = django_user_model.objects.create_user(
        username="contribute_member",
        email="contribute_member@example.com",
        password="Pass-12345!",
        email_verified_at=timezone.now(),
    )
    user.profile_completed = True
    user.save(update_fields=["profile_completed"])
    client = Client()
    client.force_login(user)
    return client


def test_the_canonical_names_and_paths() -> None:
    assert reverse("firmwares:contribute") == "/firmwares/contribute/"
    assert reverse("firmwares:upload") == "/firmwares/upload/"
    assert reverse("firmwares:my_uploads") == "/firmwares/upload/mine/"


def test_the_page_uses_the_contribute_wording(member_client: Client) -> None:
    page = member_client.get(reverse("firmwares:contribute")).content.decode()

    assert "Contribute firmware</h1>" in page
    assert "Contribution progress" in page
    assert "Upload firmware" not in page and "My uploads" not in page
    assert 'href="/contributions/firmware/"' in page


def test_the_old_upload_path_redirects_permanently(client: Client) -> None:
    response = client.get("/firmwares/upload/")

    assert response.status_code == 301
    assert response["Location"] == "/firmwares/contribute/"


def test_the_old_upload_path_keeps_the_query(client: Client) -> None:
    response = client.get("/firmwares/upload/?brand=3&model=9")

    assert response.status_code == 301
    assert response["Location"] == "/firmwares/contribute/?brand=3&model=9"


def test_anonymous_visitors_follow_the_redirect_to_sign_in(client: Client) -> None:
    response = client.get("/firmwares/upload/", follow=True)

    assert response.redirect_chain[0] == ("/firmwares/contribute/", 301)
    assert "login" in response.redirect_chain[-1][0]


def test_the_old_my_uploads_path_redirects_to_my_contributions(
    member_client: Client,
) -> None:
    response = member_client.get("/firmwares/upload/mine/?status=pending")

    assert response.status_code == 301
    assert response["Location"] == "/contributions/firmware/?status=pending"


def test_a_legacy_post_is_still_handled_not_dropped(member_client: Client) -> None:
    response = member_client.post("/firmwares/upload/", {})

    assert response.status_code == 200
    assert "Contribute firmware" in response.content.decode()


def test_catalogue_pages_offer_contribute_not_upload(member_client: Client) -> None:
    for name in ("firmwares:browse", "firmwares:library_hub", "firmwares:requests_hub"):
        page = member_client.get(reverse(name)).content.decode()
        assert 'href="/firmwares/contribute/"' in page, name
        assert "Upload firmware" not in page, name
