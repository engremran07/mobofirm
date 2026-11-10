"""Guide-request text fields use the rich-text editor of the ``guide_request`` profile."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.blog import guide_requests
from apps.blog.guide_requests_forms import GuideRequestForm
from apps.blog.models import GuideRequest
from apps.core.editor.sanitizer import text_length
from apps.site_settings.editor_models import EditorProfile

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("seeded_profiles")]

TRIED = "Flashed the stock ROM with SP Flash Tool v5.2316, error 0xC0020036."


def _form(**overrides: str) -> GuideRequestForm:
    data = {
        "title": "Bypass FRP on Infinix Hot 40",
        "kind": "method",
        "model_code": "X6836",
        "tried": f"<p>{TRIED}</p>",
        **overrides,
    }
    return GuideRequestForm(data)


@pytest.fixture
def asker(django_user_model):
    return django_user_model.objects.create_user(
        username="asker",
        email="asker@example.com",
        email_verified_at=timezone.now(),
    )


def test_tried_and_description_use_the_editor_and_the_outcome_stays_plain() -> None:
    """The long texts are editors; the one-sentence outcome is a plain input."""
    html = str(_form()["tried"]) + str(_form()["description"])
    assert html.count("data-mf-editor-config") == 2
    assert '&quot;profile&quot;:&quot;guide_request&quot;' in html
    assert 'placeholder="e.g. Flashed the stock ROM' in html
    assert "data-mf-editor" not in str(_form()["expected_outcome"])


def test_submitted_markup_is_reduced_to_what_the_profile_allows() -> None:
    """Scripts, handlers and inline styles never survive the form."""
    dirty = (
        f"<p onclick=\"x()\" style=\"color:red\">{TRIED}</p>"
        "<script>alert(1)</script><p><strong>Bold</strong> <em>step</em></p>"
    )
    form = _form(tried=dirty, description='<p>Hi <a href="javascript:x()">link</a></p>')

    assert form.is_valid(), form.errors
    tried = form.cleaned_data["tried"]
    assert "<script" not in tried and "onclick" not in tried and "style=" not in tried
    assert "<strong>Bold</strong>" in tried and TRIED in tried
    assert "javascript:" not in form.cleaned_data["description"]


def test_plain_text_is_wrapped_in_paragraphs() -> None:
    """A browser without the editor posts plain text; it is stored as paragraphs."""
    form = _form(tried=f"{TRIED}\n\nSecond paragraph here.")

    assert form.is_valid(), form.errors
    assert form.cleaned_data["tried"].count("<p>") == 2


def test_the_minimum_counts_visible_text_not_markup() -> None:
    """Heavy markup around a short text is still too short."""
    short = "<p><strong><em><u>too short</u></em></strong></p>" * 1
    form = _form(tried=short)

    assert not form.is_valid()
    assert "at least 20 characters" in form.errors["tried"][0]
    exact = "<p>" + "<b>x</b>" * 20 + "</p>"
    assert text_length(exact) == 20
    assert _form(tried=exact).is_valid()


def test_the_maximum_counts_visible_text_not_markup() -> None:
    """2000 visible characters fit however much markup wraps them."""
    wrapped = "<p>" + "<b>a</b>" * guide_requests.MAX_TEXT_LENGTH + "</p>"
    assert len(wrapped) > guide_requests.MAX_TEXT_LENGTH * 5
    assert _form(tried=wrapped).is_valid()

    too_long = "<p>" + "a" * (guide_requests.MAX_TEXT_LENGTH + 1) + "</p>"
    form = _form(tried=too_long)
    assert not form.is_valid()
    assert "at most" in form.errors["tried"][0]
    assert not _form(description=too_long).is_valid()


def test_a_disabled_profile_leaves_a_plain_textarea_that_still_sanitises() -> None:
    """Switching the editor off keeps the form working and the output safe."""
    EditorProfile.objects.filter(context="guide_request").update(enabled=False)

    form = _form(tried=f"<p>{TRIED}</p><script>x</script>")
    markup = str(form["tried"])
    assert "<textarea" in markup and "data-mf-editor" not in markup
    assert form.is_valid(), form.errors
    assert "<script" not in form.cleaned_data["tried"]


def test_the_service_sanitises_and_counts_visible_text(asker) -> None:
    """Callers other than the form (API, tests) get the same guarantees."""
    context = guide_requests.RequestContext(model_code="X6836")
    with pytest.raises(guide_requests.GuideRequestError, match="at least 20"):
        guide_requests.create_request(
            asker,
            guide_requests.RequestText(
                title="Unlock the pattern on Hot 40",
                kind="pattern",
                tried="<p><b>short</b></p><p><i>text</i></p>",
            ),
            context,
        )
    with pytest.raises(guide_requests.GuideRequestError, match="must not exceed"):
        guide_requests.create_request(
            asker,
            guide_requests.RequestText(
                title="Unlock the pattern on Hot 40",
                kind="pattern",
                tried=TRIED,
                description="a" * (guide_requests.MAX_TEXT_LENGTH + 1),
            ),
            context,
        )

    item = guide_requests.create_request(
        asker,
        guide_requests.RequestText(
            title="Unlock the pattern on Hot 40",
            kind="pattern",
            tried=f"<p>{TRIED}</p><script>x</script>",
            description="Plain <b>details</b>",
        ),
        context,
    )
    assert "<script" not in item.tried and TRIED in item.tried
    assert item.description == "Plain <b>details</b>"


def test_the_request_page_renders_the_stored_html_safely(asker, client) -> None:
    """Formatting shows on the request page; injected markup does not."""
    item = GuideRequest.objects.create(
        requester=asker,
        title="Unlock the pattern on Hot 40",
        kind="pattern",
        model_code="X6836",
        tried="<p>Tried <strong>everything</strong></p><script>alert(1)</script>",
        description="Legacy plain details\n\nSecond line",
    )
    client.force_login(asker)

    body = client.get(item.get_absolute_url()).content.decode()

    assert "<strong>everything</strong>" in body
    assert "<script>alert(1)</script>" not in body
    assert "<p>Legacy plain details</p>" in body
    assert reverse("blog:request_list") in body


def test_the_staff_queue_shows_rendered_text_not_markup(
    django_user_model, asker, client
) -> None:
    """The read-only admin page renders the formatted text."""
    admin_user = django_user_model.objects.create_superuser(
        username="boss", email="boss@example.com", email_verified_at=timezone.now()
    )
    item = GuideRequest.objects.create(
        requester=asker,
        title="Unlock the pattern on Hot 40",
        kind="pattern",
        model_code="X6836",
        tried="<p>Tried <strong>everything</strong></p>",
    )
    client.force_login(admin_user)

    response = client.get(reverse("admin:blog_guiderequest_change", args=[item.pk]))

    assert response.status_code == 200
    assert "<strong>everything</strong>" in response.content.decode()
