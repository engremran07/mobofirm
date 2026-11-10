"""Profile storage, resolution, seeding and the feature-derived server policy."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.core.editor import get_profile, sanitize_rich_text
from apps.core.editor.spec import CONTEXT_LABELS, default_profile
from apps.site_settings.editor_models import EditorProfile
from apps.site_settings.editor_store import clear_profile_cache

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]


def test_seeding_creates_every_context_with_the_code_defaults() -> None:
    """Seeding creates one profile per context equal to the code defaults."""
    assert set(EditorProfile.objects.values_list("context", flat=True)) == set(
        CONTEXT_LABELS
    )
    for context in CONTEXT_LABELS:
        assert EditorProfile.objects.get(context=context).to_spec() == default_profile(
            context
        )


def test_seed_command_is_idempotent_and_reset_restores_defaults() -> None:
    """Seeding twice changes nothing; ``--reset`` undoes admin edits."""
    EditorProfile.objects.filter(context="comment").update(
        features=["bold"], height=500
    )
    call_command("seed_editor_profiles", verbosity=0)
    edited = EditorProfile.objects.get(context="comment")
    assert edited.height == 500
    EditorProfile.objects.all().delete()
    call_command("seed_editor_profiles", verbosity=0)
    call_command("seed_editor_profiles", verbosity=0)
    assert EditorProfile.objects.count() == len(CONTEXT_LABELS)
    EditorProfile.objects.filter(context="comment").update(height=500)
    call_command("seed_editor_profiles", "--reset", verbosity=0)
    assert EditorProfile.objects.get(context="comment").height == 160


def test_database_profile_overrides_the_code_default() -> None:
    """Resolution prefers the administrator's row and tracks edits at once."""
    assert get_profile("comment").allows("links")
    row = EditorProfile.objects.get(context="comment")
    row.features = ["bold"]
    row.save()
    assert get_profile("comment").features == frozenset({"bold"})
    row.features = ["bold", "links"]
    row.save()
    assert get_profile("comment").allows("links")


def test_missing_row_falls_back_to_the_code_default() -> None:
    """Deleting a row (or an unknown context) still resolves a profile."""
    EditorProfile.objects.filter(context="request").delete()
    clear_profile_cache()
    assert get_profile("request") == default_profile("request")
    assert get_profile("brand-new-context").key == "brand-new-context"


def test_resolved_values_are_clamped_to_supported_ranges() -> None:
    """Out-of-range stored values never reach the editor or the sanitiser."""
    EditorProfile.objects.filter(context="page").update(
        height=5000, upload_max_kb=999_999, features=["bold", "nonsense"]
    )
    clear_profile_cache()
    spec = get_profile("page")
    assert spec.layout.height == 900
    assert spec.upload.max_kb == 10240
    assert spec.features == frozenset({"bold"})


def test_sanitiser_follows_the_database_profile() -> None:
    """Switching a feature off in the admin removes its markup on save/render."""
    html = '<p><strong>b</strong> <a href="https://x.example">l</a></p>'
    assert "<a " in sanitize_rich_text(html, "comment")
    row = EditorProfile.objects.get(context="comment")
    row.features = ["bold"]
    row.save()
    cleaned = sanitize_rich_text(html, "comment")
    assert "<a " not in cleaned
    assert "<strong>" in cleaned


def test_upload_without_images_is_rejected_by_the_model() -> None:
    """Uploads need the images feature; video needs a provider."""
    row = EditorProfile.objects.get(context="comment")
    row.upload_enabled = True
    with pytest.raises(ValidationError) as images:
        row.full_clean()
    assert "upload_enabled" in images.value.message_dict
    row.upload_enabled = False
    row.features = ["video"]
    row.video_providers = []
    with pytest.raises(ValidationError) as video:
        row.full_clean()
    assert "video_providers" in video.value.message_dict


def test_reset_to_defaults_restores_every_field() -> None:
    """The model-level reset used by the admin action."""
    row = EditorProfile.objects.get(context="blog_post")
    row.features = []
    row.enabled = False
    row.upload_enabled = False
    row.reset_to_defaults()
    row.save()
    assert EditorProfile.objects.get(context="blog_post").to_spec() == default_profile(
        "blog_post"
    )
