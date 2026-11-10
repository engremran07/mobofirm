"""Firmware release notes, flashing-tool instructions and request details use the editor."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.core.editor.sanitizer import text_length
from apps.firmwares.catalog_lookup import DETAILS_MAX, CatalogRequestError
from apps.firmwares.catalog_requests import clean_details, submit_brand_request
from apps.firmwares.forms import (
    BrandRequestForm,
    FirmwareRequestForm,
    UploadForm,
)
from apps.firmwares.models import (
    BrandCreationRequest,
    FlashingTool,
    FlashingToolCategory,
)
from apps.firmwares.services import RELEASE_INFO_LIMITS, submission_metadata
from apps.site_settings.editor_models import EditorProfile

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]


def _changelog(value: str) -> str:
    return UploadForm.base_fields["changelog"].clean(value)


def test_the_upload_changelog_is_a_firmware_notes_editor_and_notes_stay_plain() -> None:
    """The public release notes are rich; the reviewer note stays a textarea."""
    form = UploadForm()
    changelog, notes = str(form["changelog"]), str(form["notes"])

    assert "data-mf-editor-config" in changelog
    assert "&quot;profile&quot;:&quot;firmware_notes&quot;" in changelog
    assert "&quot;maxLength&quot;:5000" in changelog
    assert "data-mf-editor" not in notes


def test_the_changelog_is_sanitised_and_counts_visible_text() -> None:
    """Markup is reduced to the profile; only visible characters are counted."""
    cleaned = _changelog(
        '<p onclick="x()">Fixed <strong>VoLTE</strong></p><script>1</script>'
        '<img src="/media/x.png">'
    )
    assert cleaned == "<p>Fixed <strong>VoLTE</strong></p>"

    limit = RELEASE_INFO_LIMITS["changelog"]
    assert _changelog("<p>" + "<b>a</b>" * limit + "</p>")
    with pytest.raises(Exception, match="at most"):
        _changelog("a" * (limit + 1))


def test_a_disabled_profile_keeps_a_plain_changelog_textarea() -> None:
    """With the editor off the field is a textarea and still sanitised."""
    EditorProfile.objects.filter(context="firmware_notes").update(enabled=False)

    assert "data-mf-editor" not in str(UploadForm()["changelog"])
    assert _changelog("Line one\n\nLine two") == "<p>Line one</p>\n\n<p>Line two</p>"


def test_submission_metadata_stores_sanitised_changelog_within_the_visible_limit() -> (
    None
):
    """The service guarantees the same for callers that skip the form."""
    metadata = submission_metadata(
        None, {"changelog": "<p>Fix</p><script>x</script>", "build_number": "B1"}
    )
    assert metadata == {"changelog": "<p>Fix</p>", "build_number": "B1"}

    wrapped = "<p>" + "<b>a</b>" * RELEASE_INFO_LIMITS["changelog"] + "</p>"
    assert submission_metadata(None, {"changelog": wrapped})["changelog"] == wrapped
    with pytest.raises(ValueError, match="too long"):
        submission_metadata(
            None, {"changelog": "a" * (RELEASE_INFO_LIMITS["changelog"] + 1)}
        )


def test_request_details_are_rich_fields_of_the_request_profile() -> None:
    """Every catalogue and firmware request note uses the same editor profile."""
    brand = str(BrandRequestForm()["details"])
    firmware = str(FirmwareRequestForm()["description"])

    for markup in (brand, firmware):
        assert "&quot;profile&quot;:&quot;request&quot;" in markup
        assert "&quot;maxLength&quot;:1000" in markup
        assert 'placeholder="' in markup


def test_request_details_are_sanitised_and_counted_by_visible_text() -> None:
    """Details keep basic formatting, lose scripts and count only visible text."""
    form = BrandRequestForm(
        {"name": "Acme", "details": "<p>See <b>site</b></p><script>x</script>"}
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["details"] == "<p>See <b>site</b></p>"

    wrapped = "<p>" + "<b>a</b>" * DETAILS_MAX + "</p>"
    assert BrandRequestForm({"name": "Acme", "details": wrapped}).is_valid()
    assert not BrandRequestForm(
        {"name": "Acme", "details": "a" * (DETAILS_MAX + 1)}
    ).is_valid()


def test_the_request_service_stores_sanitised_details(make_user) -> None:
    """The service is the single guard for API and form callers."""
    user = make_user("details-user")
    pending = submit_brand_request(
        user, name="Acme", details="<p>Hi</p><script>alert(1)</script>"
    )
    assert pending.details == "<p>Hi</p>"
    assert text_length(clean_details("<p>" + "a" * DETAILS_MAX + "</p>")) == DETAILS_MAX
    with pytest.raises(CatalogRequestError, match="must not exceed"):
        clean_details("a" * (DETAILS_MAX + 1))


def test_decided_requests_show_rendered_details_in_the_admin(make_user, client) -> None:
    """Frozen request pages render the formatted text instead of raw markup."""
    boss = make_user("details-admin", superuser=True)
    pending = BrandCreationRequest.objects.create(
        name="Acme",
        details="<p>Visit <strong>acme.example</strong></p>",
        requested_by=boss,
        status="rejected",
    )
    client.force_login(boss)

    response = client.get(
        reverse("admin:firmwares_brandcreationrequest_change", args=[pending.pk])
    )

    assert response.status_code == 200
    assert "<strong>acme.example</strong>" in response.content.decode()


def test_the_flashing_tool_admin_edits_instructions_with_the_editor(
    make_user, client
) -> None:
    """Instructions are rich; the short description (used in cards) is not."""
    boss = make_user("tool-admin", superuser=True)
    category = FlashingToolCategory.objects.create(name="Flash", slug="flash")
    tool = FlashingTool.objects.create(
        category=category, name="SP Flash", slug="sp-flash", instructions="Step one"
    )
    client.force_login(boss)

    page = client.get(
        reverse("admin:firmwares_flashingtool_change", args=[tool.pk])
    ).content.decode()

    assert page.count("data-mf-editor-config") == 1
    assert "&quot;profile&quot;:&quot;firmware_notes&quot;" in page
    assert "<p>Step one</p>" in page
