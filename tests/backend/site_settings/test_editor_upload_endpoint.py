"""Editor image upload endpoint: auth, permissions, quota, ownership, orphans."""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.site_settings.editor_models import EditorImage, EditorProfile
from apps.site_settings.editor_store import clear_profile_cache
from apps.site_settings.editor_uploads import may_upload

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]
PASSWORD = "Pa55-word-for-tests"


@pytest.fixture(autouse=True)
def isolated_media(settings: Any, tmp_path: Path) -> None:
    """Keep uploaded files in a throw-away directory."""
    settings.MEDIA_ROOT = tmp_path


def _png(size: tuple[int, int] = (30, 20)) -> SimpleUploadedFile:
    buffer = BytesIO()
    Image.new("RGB", size, "blue").save(buffer, "PNG")
    return SimpleUploadedFile("a.png", buffer.getvalue(), content_type="image/png")


def _client(*, staff: bool = False, name: str = "writer") -> Client:
    user = get_user_model().objects.create_user(
        username=name, email=f"{name}@example.org", password=PASSWORD, is_staff=staff
    )
    client = Client()
    client.force_login(user)
    return client


def _post(client: Client, context: str, upload: SimpleUploadedFile | None = None):
    return client.post(
        reverse("site_settings:editor_upload"),
        {"context": context, "file": upload or _png()},
    )


def test_anonymous_users_cannot_upload() -> None:
    """The endpoint requires a signed-in user."""
    response = _post(Client(), "guide_request")
    assert response.status_code == 401
    assert EditorImage.objects.count() == 0


def test_get_is_not_allowed() -> None:
    """Uploads are POST only."""
    assert _client().get(reverse("site_settings:editor_upload")).status_code == 405


def test_upload_stores_an_owned_random_named_image_under_the_prefix() -> None:
    """A valid upload is re-encoded, owned and served from ``editor/``."""
    client = _client()
    response = _post(client, "guide_request")
    assert response.status_code == 201, response.content
    body = response.json()
    image = EditorImage.objects.get(pk=body["id"])
    assert image.owner.username == "writer"
    assert image.file.name.startswith("editor/")
    assert re.fullmatch(r"editor/\d{4}/\d{2}/[0-9a-f]{32}\.png", image.file.name)
    assert body["url"].endswith(image.file.name)
    assert (image.width, image.height) == (30, 20)
    assert Path(image.file.path).is_file()


def test_contexts_without_upload_are_forbidden() -> None:
    """Comments (and unknown contexts) have uploads switched off."""
    client = _client()
    assert _post(client, "comment").status_code == 403
    assert _post(client, "no-such-context").status_code == 403
    assert EditorImage.objects.count() == 0


def test_uploads_can_be_switched_off_in_the_admin() -> None:
    """Toggling upload off (or master off) blocks the endpoint immediately."""
    client = _client()
    EditorProfile.objects.filter(context="guide_request").update(upload_enabled=False)
    clear_profile_cache()
    assert _post(client, "guide_request").status_code == 403
    EditorProfile.objects.filter(context="guide_request").update(
        upload_enabled=True, enabled=False
    )
    clear_profile_cache()
    assert _post(client, "guide_request").status_code == 403


def test_staff_only_contexts_reject_regular_users() -> None:
    """Page/announcement uploads need staff."""
    assert _post(_client(), "page").status_code == 403
    assert _post(_client(staff=True, name="boss"), "page").status_code == 201


def test_blog_uploads_need_the_blog_permission() -> None:
    """A plain user cannot upload for blog posts; staff can."""
    plain = get_user_model().objects.create_user(
        username="plain", email="p@example.org", password=PASSWORD
    )
    assert not may_upload(plain, "blog_post")
    staff = get_user_model().objects.create_user(
        username="st", email="s@example.org", password=PASSWORD, is_staff=True
    )
    assert may_upload(staff, "blog_post")


def test_invalid_and_oversized_files_are_rejected() -> None:
    """The endpoint reports validation problems as 400 with a code."""
    client = _client()
    bad = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    response = _post(client, "guide_request", bad)
    assert response.status_code == 400
    assert response.json()["code"] == "invalid"
    EditorProfile.objects.filter(context="guide_request").update(upload_max_kb=1)
    clear_profile_cache()
    big = BytesIO()
    Image.new("RGB", (600, 600), "red").save(big, "PNG", compress_level=0)
    too_big = SimpleUploadedFile("b.png", big.getvalue(), content_type="image/png")
    assert _post(client, "guide_request", too_big).json()["code"] == "too_large"


def test_daily_quota_applies_to_regular_users_only() -> None:
    """The profile's daily limit blocks further uploads with HTTP 429."""
    EditorProfile.objects.filter(context="guide_request").update(upload_daily_limit=2)
    clear_profile_cache()
    client = _client()
    assert _post(client, "guide_request").status_code == 201
    assert _post(client, "guide_request").status_code == 201
    third = _post(client, "guide_request")
    assert third.status_code == 429
    assert third.json()["code"] == "quota"
    EditorProfile.objects.filter(context="announcement").update(upload_daily_limit=1)
    clear_profile_cache()
    staff = _client(staff=True, name="boss")
    assert _post(staff, "announcement").status_code == 201
    assert _post(staff, "announcement").status_code == 201
