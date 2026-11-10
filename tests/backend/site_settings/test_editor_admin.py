"""The Editor profiles admin: forms, validation, actions, preview, navigation."""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.admin.sites import site as admin_site
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.core.admin_navigation import NAV_GROUPS
from apps.core.editor import get_profile
from apps.core.editor.spec import CONTEXT_LABELS, default_profile
from apps.site_settings.editor_models import EditorImage, EditorProfile

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]
PASSWORD = "Pa55-word-for-tests"


@pytest.fixture(name="staff")
def staff_client() -> Client:
    """A signed-in superuser."""
    user = get_user_model().objects.create_superuser(
        username="root", email="root@example.org", password=PASSWORD
    )
    client = Client()
    client.force_login(user)
    return client


def _post_data(profile: EditorProfile, **changes: object) -> dict[str, object]:
    data: dict[str, object] = {
        "enabled": "on" if profile.enabled else "",
        "features": list(profile.features),
        "video_providers": list(profile.video_providers),
        "upload_max_kb": profile.upload_max_kb,
        "upload_daily_limit": profile.upload_daily_limit,
        "max_length": profile.max_length,
        "spellcheck": "on" if profile.spellcheck else "",
        "placeholder": profile.placeholder,
        "height": profile.height,
    }
    if profile.upload_enabled:
        data["upload_enabled"] = "on"
    data.update(changes)
    return {key: value for key, value in data.items() if value != ""}


def test_models_are_registered_and_listed_in_the_system_nav_group() -> None:
    """Both models are in the admin and in the System sidebar group."""
    assert admin_site.is_registered(EditorProfile)
    assert admin_site.is_registered(EditorImage)
    system = next(group for group in NAV_GROUPS if group.title == "System")
    labels = {entry.label for entry in system.entries if hasattr(entry, "label")}
    assert {"site_settings.editorprofile", "site_settings.editorimage"} <= labels


def test_changelist_and_every_change_form_render(staff: Client) -> None:
    """Each context has a working change page with a preview editor."""
    response = staff.get(reverse("admin:site_settings_editorprofile_changelist"))
    assert response.status_code == 200
    for label in CONTEXT_LABELS.values():
        assert label in response.content.decode()
    for profile in EditorProfile.objects.all():
        url = reverse("admin:site_settings_editorprofile_change", args=[profile.pk])
        page = staff.get(url)
        assert page.status_code == 200, profile.context
        html = page.content.decode()
        assert "data-mf-editor-config" in html
        assert "Toolbar and formatting features" in html
        assert "Text formatting" in html
        assert 'name="features"' in html


def test_preview_reflects_the_saved_profile(staff: Client) -> None:
    """The preview editor is built from the stored features."""
    profile = EditorProfile.objects.get(context="comment")
    url = reverse("admin:site_settings_editorprofile_change", args=[profile.pk])
    html = staff.get(url).content.decode()
    match = re.search(r"data-mf-editor-config=\"([^\"]*)\"", html)
    assert match
    config = json.loads(match.group(1).replace("&quot;", '"'))
    assert config["profile"] == "comment"
    assert set(config["features"]) == set(profile.features)


def test_saving_changes_features_and_the_resolved_profile(staff: Client) -> None:
    """Unchecking a feature removes it from the resolved profile at once."""
    profile = EditorProfile.objects.get(context="blog_post")
    assert get_profile("blog_post").allows("tables")
    features = [key for key in profile.features if key != "tables"]
    response = staff.post(
        reverse("admin:site_settings_editorprofile_change", args=[profile.pk]),
        _post_data(profile, features=features),
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    assert not get_profile("blog_post").allows("tables")
    profile.refresh_from_db()
    assert profile.updated_by is not None


def test_upload_requires_the_images_feature(staff: Client) -> None:
    """The form rejects uploads without images and video without providers."""
    profile = EditorProfile.objects.get(context="blog_post")
    url = reverse("admin:site_settings_editorprofile_change", args=[profile.pk])
    features = [key for key in profile.features if key != "images"]
    response = staff.post(url, _post_data(profile, features=features))
    assert response.status_code == 200
    assert "Image uploads need the Images feature." in response.content.decode()
    response = staff.post(url, _post_data(profile, video_providers=[]))
    assert response.status_code == 200
    assert "Choose at least one provider" in response.content.decode()


def test_reset_enable_and_disable_actions(staff: Client) -> None:
    """Bulk actions reset to defaults and flip the master switch."""
    url = reverse("admin:site_settings_editorprofile_changelist")
    comment = EditorProfile.objects.get(context="comment")
    EditorProfile.objects.filter(pk=comment.pk).update(features=["bold"], height=777)
    staff.post(
        url,
        {"action": "reset_to_defaults", "_selected_action": [comment.pk]},
        follow=True,
    )
    assert EditorProfile.objects.get(pk=comment.pk).to_spec() == default_profile(
        "comment"
    )
    staff.post(url, {"action": "disable_editors", "_selected_action": [comment.pk]})
    assert not get_profile("comment").enabled
    staff.post(url, {"action": "enable_editors", "_selected_action": [comment.pk]})
    assert get_profile("comment").enabled


def test_profiles_cannot_be_deleted_and_added_only_when_missing(
    staff: Client,
) -> None:
    """Deletion is blocked; add is offered only for a context without a row."""
    profile = EditorProfile.objects.get(context="page")
    delete_url = reverse("admin:site_settings_editorprofile_delete", args=[profile.pk])
    assert staff.get(delete_url).status_code == 403
    add_url = reverse("admin:site_settings_editorprofile_add")
    assert staff.get(add_url).status_code == 403
    EditorProfile.objects.filter(context="page").delete()
    assert staff.get(add_url).status_code == 200


def test_regular_staff_without_permission_cannot_open_the_admin() -> None:
    """Staff lacking model permissions get a 403."""
    user = get_user_model().objects.create_user(
        username="editor", email="e@example.org", password=PASSWORD, is_staff=True
    )
    client = Client()
    client.force_login(user)
    url = reverse("admin:site_settings_editorprofile_changelist")
    assert client.get(url).status_code == 403


def test_editor_image_admin_lists_images(staff: Client) -> None:
    """The images changelist renders (read-only, no add)."""
    url = reverse("admin:site_settings_editorimage_changelist")
    assert staff.get(url).status_code == 200
    assert staff.get(reverse("admin:site_settings_editorimage_add")).status_code == 403
