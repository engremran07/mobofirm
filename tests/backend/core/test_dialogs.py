"""Contract tests for the core dialog shell (apps/core/dialogs.py + components)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, TypedDict

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.test import Client, RequestFactory
from django.urls import clear_url_caches, path, reverse
from django.utils import timezone

from app import urls as root_urls
from apps.core.dialogs import (
    ConfirmDialogForm,
    DialogError,
    DialogForm,
    DialogView,
    dialog_success,
)


class HtmxExtra(TypedDict):
    HTTP_HX_REQUEST: str


HTMX = HtmxExtra(HTTP_HX_REQUEST="true")
PERFORMED: list[str] = []


class ReasonForm(DialogForm):
    reason_required = True


class DeleteDialog(DialogView):
    form_class = ReasonForm
    dialog_title = "Delete the thing"
    dialog_message = "This cannot be undone."
    confirm_label = "Delete"
    danger = True
    success_url = "/done/"
    success_message = "Deleted."

    def has_permission(self, request) -> bool:
        return bool(request.user.is_staff)

    def perform(self, form: DialogForm) -> None:
        if form.reason == "boom":
            get_user_model().objects.create_user(
                username="created-in-dialog", email="created@example.com", password=None
            )
            raise DialogError("A rule rejected this.")
        PERFORMED.append(form.reason)


class PhraseForm(DialogForm):
    confirm_phrase = "DELETE"


class PhraseDialog(DialogView):
    form_class = PhraseForm
    dialog_title = "Type to confirm"
    success_url = "/done/"

    def perform(self, form: DialogForm) -> None:
        PERFORMED.append("phrase")


class PlainDialog(DialogView):
    form_class = ConfirmDialogForm

    def perform(self, form: DialogForm) -> None:
        PERFORMED.append("plain")


@pytest.fixture(autouse=True)
def dialog_urls() -> Iterator[None]:
    PERFORMED.clear()
    root = root_urls
    original = list(root.urlpatterns)
    root.urlpatterns = [
        path("test-dialogs/delete/", DeleteDialog.as_view(), name="test_dialog_delete"),
        path("test-dialogs/phrase/", PhraseDialog.as_view(), name="test_dialog_phrase"),
        path("test-dialogs/plain/", PlainDialog.as_view(), name="test_dialog_plain"),
        *original,
    ]
    clear_url_caches()
    yield
    root.urlpatterns = original
    clear_url_caches()


def make_user(username: str, *, staff: bool = False):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=None,
        is_staff=staff,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def staff_client(db) -> Client:
    client = Client()
    client.force_login(make_user("dialog-staff", staff=True))
    return client


@pytest.mark.django_db
def test_anonymous_is_sent_to_login_for_htmx_and_plain_requests():
    url = reverse("test_dialog_delete")
    fragment = Client().get(url, **HTMX)
    assert fragment.status_code == 204
    assert fragment["HX-Redirect"].startswith(settings.LOGIN_URL)
    page = Client().get(url)
    assert page.status_code == 302
    assert page["Location"].startswith(settings.LOGIN_URL)


@pytest.mark.django_db
def test_permission_hook_rejects_non_staff_for_get_and_post():
    client = Client()
    client.force_login(make_user("dialog-member"))
    url = reverse("test_dialog_delete")
    assert client.get(url, **HTMX).status_code == 403
    assert client.post(url, {"reason": "x"}, **HTMX).status_code == 403
    assert PERFORMED == []


@pytest.mark.django_db
def test_get_renders_an_accessible_htmx_fragment(staff_client):
    response = staff_client.get(reverse("test_dialog_delete"), **HTMX)
    body = response.content.decode()
    assert response.status_code == 200
    assert "<main" not in body
    assert '<dialog class="app-dialog app-dialog--sm app-dialog--danger"' in body
    assert 'aria-labelledby="app-dialog-title"' in body
    assert 'aria-describedby="app-dialog-message"' in body
    assert "data-dialog-auto-open" in body
    assert 'data-dialog-backdrop="static"' in body
    assert 'hx-post="/test-dialogs/delete/"' in body
    assert 'hx-target="closest dialog"' in body
    assert "csrfmiddlewaretoken" in body
    assert "btn-danger" in body
    assert "data-dialog-inline" not in body


@pytest.mark.django_db
def test_get_without_javascript_renders_the_same_form_in_a_page(staff_client):
    response = staff_client.get(reverse("test_dialog_delete"))
    body = response.content.decode()
    assert response.status_code == 200
    assert '<main id="main-content"' in body
    assert "data-dialog-inline" in body
    assert "hx-post" not in body
    assert 'id="dialog-slot"' in body
    assert 'id="app-dialog-template"' in body


@pytest.mark.django_db
def test_invalid_post_rerenders_the_dialog_with_errors(staff_client):
    response = staff_client.post(reverse("test_dialog_delete"), {"reason": ""}, **HTMX)
    body = response.content.decode()
    assert response.status_code == 200
    assert "invalid-feedback" in body
    assert "data-dialog-auto-open" in body
    assert "HX-Trigger" not in response
    assert PERFORMED == []


@pytest.mark.django_db
def test_valid_htmx_post_signals_close_done_and_redirect(staff_client):
    response = staff_client.post(
        reverse("test_dialog_delete"), {"reason": "cleanup"}, **HTMX
    )
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {
        "dialog:close": {},
        "dialog:done": {},
    }
    assert response["HX-Redirect"] == "/done/"
    assert PERFORMED == ["cleanup"]
    assert [str(m) for m in get_messages(response.wsgi_request)] == ["Deleted."]


@pytest.mark.django_db
def test_valid_plain_post_is_post_redirect_get(staff_client):
    response = staff_client.post(reverse("test_dialog_delete"), {"reason": "cleanup"})
    assert response.status_code == 302
    assert response["Location"] == "/done/"


@pytest.mark.django_db
def test_without_a_success_url_htmx_refreshes_the_page(staff_client):
    response = staff_client.post(reverse("test_dialog_plain"), {}, **HTMX)
    assert response.status_code == 204
    assert response["HX-Refresh"] == "true"
    assert "HX-Redirect" not in response


@pytest.mark.django_db
def test_dialog_error_rolls_back_and_shows_inside_the_dialog(staff_client):
    response = staff_client.post(
        reverse("test_dialog_delete"), {"reason": "boom"}, **HTMX
    )
    assert response.status_code == 200
    assert "A rule rejected this." in response.content.decode()
    assert not get_user_model().objects.filter(username="created-in-dialog").exists()
    assert PERFORMED == []


@pytest.mark.django_db
def test_typed_confirmation_is_enforced_server_side(staff_client):
    url = reverse("test_dialog_phrase")
    page = staff_client.get(url, **HTMX).content.decode()
    assert 'data-dialog-phrase="DELETE"' in page
    assert 'name="confirmation"' in page
    assert ':disabled="submitBlocked"' in page
    wrong = staff_client.post(url, {"confirmation": "delete"}, **HTMX)
    assert wrong.status_code == 200
    assert PERFORMED == []
    right = staff_client.post(url, {"confirmation": "DELETE"}, **HTMX)
    assert right.status_code == 204
    assert PERFORMED == ["phrase"]


@pytest.mark.django_db
def test_next_parameter_is_honoured_only_for_this_host(staff_client):
    url = reverse("test_dialog_delete")
    local = staff_client.post(f"{url}?next=/library/", {"reason": "ok"})
    assert local["Location"] == "/library/"
    foreign = staff_client.post(f"{url}?next=https://evil.example/", {"reason": "ok"})
    assert foreign["Location"] == "/done/"


@pytest.mark.django_db
def test_post_requires_a_csrf_token(db):
    client = Client(enforce_csrf_checks=True)
    client.force_login(make_user("dialog-csrf", staff=True))
    url = reverse("test_dialog_delete")
    assert client.post(url, {"reason": "x"}, **HTMX).status_code == 403
    page = client.get(url)
    token = page.cookies["csrftoken"].value
    ok = client.post(url, {"reason": "x", "csrfmiddlewaretoken": token}, **HTMX)
    assert ok.status_code == 204
    assert PERFORMED == ["x"]


def test_dialog_success_can_stay_on_the_page_for_listeners():
    request = RequestFactory().post("/x/", HTTP_HX_REQUEST="true")
    response = dialog_success(request, payload={"id": 7}, refresh=False)
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"])["dialog:done"] == {"id": 7}
    assert "HX-Refresh" not in response
    assert "HX-Redirect" not in response


def test_staying_dialog_success_sends_its_message_as_an_immediate_toast():
    """Without a redirect or refresh the message is a ``toast`` event, not a flash."""
    request = RequestFactory().post("/x/", HTTP_HX_REQUEST="true")

    response = dialog_success(request, message="Note saved.", refresh=False)

    triggers = json.loads(response["HX-Trigger"])
    assert triggers["toast"] == {"message": "Note saved.", "level": "success"}
    assert triggers["dialog:close"] == {}
    assert "HX-Refresh" not in response


def test_dialog_success_without_htmx_redirects_to_a_safe_default():
    request = RequestFactory().post("/x/")
    response = dialog_success(request)
    assert response.status_code == 302
    assert response["Location"] == "/"


def render_confirm(**context: Any) -> str:
    request = RequestFactory().get("/")
    return render_to_string("components/confirm_dialog.html", context, request=request)


@pytest.mark.django_db
def test_confirm_dialog_component_contract():
    body = render_confirm(
        dialog_id="remove-device",
        dialog_title="Remove device?",
        dialog_message="It will be signed out.",
        dialog_action="/devices/1/remove/",
        confirm_label="Remove",
        hidden_name="device",
        hidden_value="1",
        danger=True,
        reason_label="Why?",
        reason_required=True,
        confirm_phrase="REMOVE",
    )
    assert 'id="remove-device"' in body
    assert 'aria-labelledby="remove-device-title"' in body
    assert 'aria-describedby="remove-device-message"' in body
    assert "Remove device?" in body
    assert 'action="/devices/1/remove/"' in body
    assert 'name="csrfmiddlewaretoken"' in body
    assert 'name="device" value="1"' in body
    assert 'name="reason"' in body and "required" in body
    assert 'name="confirmation"' in body
    assert 'data-dialog-backdrop="static"' in body
    assert "btn-danger" in body
    assert "hx-post" not in body


@pytest.mark.django_db
def test_confirm_dialog_component_keeps_the_legacy_parameter_names():
    body = render_confirm(
        dialog_id="legacy",
        title="Legacy title",
        message="Legacy message",
        action="/legacy/",
        confirm_label="Go",
    )
    assert "Legacy title" in body
    assert "Legacy message" in body
    assert 'action="/legacy/"' in body


@pytest.mark.django_db
def test_confirm_dialog_component_is_dismissible_when_not_destructive():
    body = render_confirm(
        dialog_id="publish",
        dialog_title="Publish?",
        dialog_message="Readers will see it.",
        dialog_action="/publish/",
        confirm_label="Publish",
    )
    assert 'data-dialog-backdrop="close"' in body
    assert "btn-primary" in body
    assert 'name="reason"' not in body
