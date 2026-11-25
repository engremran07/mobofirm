"""CRUD round trips through the console: create, update, delete, inlines, popups, autocomplete.

Every browser POST must carry the CSRF token, answer with POST-Redirect-GET on success and
re-render the form (with an announced error summary) on failure.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.db import models
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.ads.models import AdNetwork, AdUnit
from apps.core.admin_base import ModelAdmin
from apps.tags.models import Tag
from apps.wallet.admin import WorkflowOnlyAdmin
from tests.backend.admin.admin_model_factory import (
    admin_pk,
    build_row,
    registered_admins,
)

REGISTERED_ADMINS = registered_admins()
REGISTERED: list[type[models.Model]] = sorted(
    REGISTERED_ADMINS, key=lambda model: model._meta.label_lower
)
DIALOG = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "console-dialog"}


class _FormReader(HTMLParser):
    """Collects the successful controls of one <form> the way a browser would submit them."""

    def __init__(self, form_id: str) -> None:
        super().__init__()
        self.form_id = form_id
        self.inside = False
        self.data: dict[str, list[str]] = {}
        self._select: str | None = None
        self._select_multiple = False
        self._first_option: str | None = None
        self._textarea: str | None = None
        self._buffer = ""

    def _add(self, name: str, value: str) -> None:
        self.data.setdefault(name, []).append(value)

    def handle_starttag(self, tag, attrs):
        a = {key: (value or "") for key, value in attrs}
        if tag == "form":
            self.inside = a.get("id") == self.form_id
            return
        if (
            not self.inside
            or "disabled" in a
            or not a.get("name", "")
            and tag != "option"
        ):
            return
        name = a.get("name", "")
        if tag == "input":
            kind = a.get("type", "text").lower()
            if kind in {"submit", "button", "image", "file", "reset"}:
                return
            if kind in {"checkbox", "radio"} and "checked" not in a:
                return
            self._add(name, a.get("value", "on" if kind == "checkbox" else ""))
        elif tag == "select":
            self._select, self._select_multiple = name, "multiple" in a
            self._first_option = None
        elif tag == "option" and self._select:
            value = a.get("value", "")
            if self._first_option is None:
                self._first_option = value
            if "selected" in a:
                self._add(self._select, value)
        elif tag == "textarea":
            self._textarea, self._buffer = name, ""

    def handle_data(self, data):
        if self._textarea:
            self._buffer += data

    def handle_endtag(self, tag):
        if tag == "form":
            self.inside = False
        elif tag == "select" and self._select:
            if (
                not self._select_multiple
                and self._select not in self.data
                and self._first_option is not None
            ):
                self._add(self._select, self._first_option)
            self._select = None
        elif tag == "textarea" and self._textarea:
            self._add(self._textarea, self._buffer.lstrip("\n"))
            self._textarea = None


def payload(response, form_id: str, **overrides: str) -> dict[str, str | list[str]]:
    reader = _FormReader(form_id)
    reader.feed(response.content.decode())
    data: dict[str, str | list[str]] = {
        name: values if len(values) > 1 else values[0]
        for name, values in reader.data.items()
    }
    data.update(overrides)
    return data


@pytest.fixture
def superuser(db, settings):
    settings.STAFF_MFA_REQUIRED = False
    return get_user_model().objects.create_superuser(
        username="crud-trip-admin",
        email="crud-trip-admin@example.test",
        password="x-test-password-123",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def client(superuser) -> Client:
    test_client = Client()
    test_client.force_login(superuser)
    return test_client


def _url(model: type[models.Model], suffix: str, *args: object) -> str:
    return reverse(
        f"admin:{model._meta.app_label}_{model._meta.model_name}_{suffix}", args=args
    )


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_saving_an_untouched_change_form_never_breaks(model, client, superuser):
    """Submitting the form exactly as rendered redirects or re-renders with announced errors."""
    instance = build_row(model)
    change = _url(model, "change", admin_pk(instance))
    page = client.get(change)
    assert page.status_code == 200
    data = payload(page, f"{model._meta.model_name}_form", _save="Save")
    response = client.post(change, data)
    request = RequestFactory().get(change)
    request.user = superuser
    model_admin = REGISTERED_ADMINS[model]
    assert isinstance(model_admin, ModelAdmin)
    if isinstance(model_admin, WorkflowOnlyAdmin):
        assert response.status_code == 403, model._meta.label_lower
        return
    if not model_admin.has_change_permission(request, instance):
        assert response.status_code == 403, model._meta.label_lower
        return
    assert response.status_code in (200, 302), model._meta.label_lower
    if response.status_code == 200:
        html = response.content.decode()
        assert "errorlist" in html or "form-error-summary" in html, (
            f"{model._meta.label_lower}: form re-rendered without any error message"
        )
        assert 'role="alert"' in html or "aria-live" in html


@pytest.mark.django_db
def test_create_update_delete_round_trip_with_post_redirect_get(client):
    add = _url(Tag, "add")
    page = client.get(add)
    assert page.status_code == 200
    data = payload(page, "tag_form", name="Round trip", slug="round-trip", _save="Save")
    created = client.post(add, data)
    assert created.status_code == 302
    tag = Tag.objects.get(slug="round-trip")

    change = _url(Tag, "change", tag.pk)
    page = client.get(change)
    data = payload(
        page, "tag_form", description="edited by a staff member", _save="Save"
    )
    assert client.post(change, data).status_code == 302
    tag.refresh_from_db()
    assert tag.description == "edited by a staff member"
    assert client.get(_url(Tag, "history", tag.pk)).status_code == 200

    delete = _url(Tag, "delete", tag.pk)
    confirm = client.get(delete)
    assert confirm.status_code == 200 and Tag.objects.filter(pk=tag.pk).exists()
    assert "csrfmiddlewaretoken" in confirm.content.decode()
    assert client.post(delete, {"post": "yes"}).status_code == 302
    assert not Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_invalid_submission_rerenders_with_an_announced_error_summary(client):
    response = client.post(_url(Tag, "add"), {"name": "", "slug": "", "_save": "Save"})
    assert response.status_code == 200
    html = response.content.decode()
    assert "errorlist" in html or "invalid-feedback" in html
    assert 'role="alert"' in html
    assert not Tag.objects.exists()


@pytest.mark.django_db
def test_post_without_csrf_token_is_rejected(superuser):
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(superuser)
    tag = Tag.objects.create(name="Keep me", slug="keep-me")
    assert strict.post(_url(Tag, "delete", tag.pk), {"post": "yes"}).status_code == 403
    assert Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_delete_confirmation_is_a_dialog_for_htmx_and_never_deletes_on_get(client):
    tag = Tag.objects.create(name="Dialog delete", slug="dialog-delete")
    delete = _url(Tag, "delete", tag.pk)
    response = client.get(delete, **DIALOG)
    html = response.content.decode()
    assert response.status_code == 200
    assert "data-console-dialog-content" in html and "<html" not in html
    assert "csrfmiddlewaretoken" in html and "confirm(" not in html
    assert Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_popup_add_answers_with_the_popup_response_payload(client):
    add = f"{_url(Tag, 'add')}?_popup=1&_to_field=id"
    page = client.get(add)
    data = payload(page, "tag_form", name="From popup", slug="from-popup", _save="Save")
    response = client.post(add, data)
    assert response.status_code == 200
    html = response.content.decode()
    assert "data-popup-response" in html
    assert Tag.objects.filter(slug="from-popup").exists()


@pytest.mark.django_db
def test_related_widget_offers_add_and_autocomplete_returns_json(client):
    parent = Tag.objects.create(name="Parent tag", slug="parent-tag")
    page = client.get(_url(Tag, "add"))
    html = page.content.decode()
    assert "select2" not in html.lower() or "autocomplete" in html
    response = client.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "tags",
            "model_name": "tag",
            "field_name": "parent",
            "term": "Parent",
        },
    )
    assert response.status_code == 200
    body = json.loads(response.content)
    assert [item["id"] for item in body["results"]] == [str(parent.pk)]
    assert body["pagination"] == {"more": False}


@pytest.mark.django_db
def test_inline_formset_rows_are_created_and_updated(client):
    network = AdNetwork.objects.create(
        name="Network one",
        network_type=AdNetwork.NETWORK_TYPES[0][0],
    )
    change = _url(AdNetwork, "change", network.pk)
    page = client.get(change)
    assert page.status_code == 200
    data = payload(page, "adnetwork_form", _save="Save")
    prefix = "ad_units"
    assert f"{prefix}-TOTAL_FORMS" in data
    data.update(
        {
            f"{prefix}-TOTAL_FORMS": "1",
            f"{prefix}-0-name": "Unit one",
            f"{prefix}-0-unit_id": "unit-1",
            f"{prefix}-0-ad_format": "display",
            f"{prefix}-0-estimated_cpm": "0.5",
            f"{prefix}-0-is_enabled": "on",
        }
    )
    response = client.post(change, data)
    assert response.status_code == 302, response.content.decode()[:2000]
    assert AdUnit.objects.filter(network=network, name="Unit one").exists()


@pytest.mark.django_db
def test_user_password_change_page_and_post(client, superuser):
    target = get_user_model().objects.create_user(
        username="pw-target", email="pw-target@example.test", password="old-pass-12345"
    )
    url = reverse("admin:auth_user_password_change", args=[target.pk])
    page = client.get(url)
    assert page.status_code == 200
    html = page.content.decode()
    assert "csrfmiddlewaretoken" in html and "password1" in html
    bad = client.post(url, {"password1": "short", "password2": "other"})
    assert bad.status_code == 200 and "errorlist" in bad.content.decode()
    ok = client.post(
        url,
        {"password1": "A-new-passphrase-8841", "password2": "A-new-passphrase-8841"},
    )
    assert ok.status_code == 302
    target.refresh_from_db()
    assert target.check_password("A-new-passphrase-8841")


@pytest.mark.django_db
def test_changelist_announces_results_and_selection_through_live_regions(client):
    Tag.objects.create(name="Live region", slug="live-region")
    html = client.get(_url(Tag, "changelist")).content.decode()
    assert 'aria-live="polite"' in html
    assert 'id="c-cl-status"' in html
    assert "csrfmiddlewaretoken" in html
