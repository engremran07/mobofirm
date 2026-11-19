"""Every registered ModelAdmin renders its console CRUD pages (list, add, change, history, delete).

Beyond "200 OK" the pages must honour the console contract: server-rendered with
Django's own templates overridden 1:1, CSRF on every POST form, no executable inline
script, no inline event handlers or styles (strict CSP), no stock Django admin assets
or native confirm dialogs, and the HTMX/dialog variants answer with their fragments.
"""

from __future__ import annotations

import re

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import models
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.ads.models import Campaign
from apps.firmwares.models import Brand
from tests.backend.admin.admin_model_factory import admin_pk, build_row

REGISTERED: list[type[models.Model]] = sorted(
    admin.site._registry, key=lambda model: model._meta.label_lower
)

INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*(?:\bsrc=|type=\"application/json\"))", re.I)
INLINE_HANDLER = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.I)
INLINE_STYLE = re.compile(r"<[a-z][^>]*\sstyle\s*=", re.I)
POST_FORM = re.compile(r"<form\b[^>]*\bmethod=\"post\"", re.I)
STOCK_ASSET = re.compile(r"/static/admin/(?:js|css)/", re.I)
NATIVE_DIALOG = re.compile(r"\b(?:confirm|alert|prompt)\(")


@pytest.fixture
def superuser(db):
    return get_user_model().objects.create_superuser(
        username="crud-render-admin",
        email="crud-render-admin@example.test",
        password="x-test-password-123",
    )


@pytest.fixture
def client(superuser) -> Client:
    test_client = Client()
    test_client.force_login(superuser)
    return test_client


def _name(model: type[models.Model], suffix: str) -> str:
    return f"admin:{model._meta.app_label}_{model._meta.model_name}_{suffix}"


def _request_for(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def assert_console_markup(response, label: str) -> str:
    """Contract checks every console page must pass; returns the decoded HTML."""
    html = response.content.decode()
    assert INLINE_SCRIPT.search(html) is None, f"{label}: executable inline <script>"
    assert INLINE_HANDLER.search(html) is None, f"{label}: inline event handler"
    assert INLINE_STYLE.search(html) is None, f"{label}: inline style attribute"
    assert STOCK_ASSET.search(html) is None, f"{label}: stock Django admin asset"
    assert NATIVE_DIALOG.search(html) is None, f"{label}: native confirm/alert/prompt"
    if POST_FORM.search(html):
        assert "csrfmiddlewaretoken" in html, f"{label}: POST form without CSRF token"
    return html


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_list_and_add_pages_follow_the_console_contract(model, client, superuser):
    label = model._meta.label_lower
    model_admin = admin.site._registry[model]
    listing = client.get(reverse(_name(model, "changelist")))
    assert listing.status_code == 200, label
    assert_console_markup(listing, f"{label} list")
    if model_admin.has_add_permission(_request_for(superuser)):
        add = client.get(reverse(_name(model, "add")))
        assert add.status_code == 200, label
        assert_console_markup(add, f"{label} add")


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_change_history_and_delete_pages_follow_the_console_contract(
    model, client, superuser
):
    label = model._meta.label_lower
    instance = build_row(model)
    pk = admin_pk(instance)
    model_admin = admin.site._registry[model]
    change = client.get(reverse(_name(model, "change"), args=[pk]))
    assert change.status_code == 200, label
    assert_console_markup(change, f"{label} change")
    history = client.get(reverse(_name(model, "history"), args=[pk]))
    assert history.status_code == 200, label
    assert_console_markup(history, f"{label} history")
    if model_admin.has_delete_permission(_request_for(superuser), instance):
        delete = client.get(reverse(_name(model, "delete"), args=[pk]))
        assert delete.status_code == 200, label
        html = assert_console_markup(delete, f"{label} delete")
        assert 'name="post"' in html or "protected" in html or "permission" in html
        assert model._default_manager.filter(pk=instance.pk).exists(), label


@pytest.mark.django_db
def test_results_region_and_out_of_band_targets_exist(client):
    build_row(Campaign)
    html = client.get(reverse(_name(Campaign, "changelist"))).content.decode()
    for hook in (
        'id="c-changelist"',
        'id="c-cl-results"',
        'id="c-cl-chips"',
        'id="c-cl-status"',
        "data-console-changelist",
        "data-console-row-select",
        "data-console-select-all",
        "data-console-bulkbar",
    ):
        assert hook in html, hook
    assert 'hx-select="#c-cl-results"' in html
    assert 'id="c-cl-filters"' in html
    assert 'id="c-cl-params"' in html


@pytest.mark.django_db
def test_htmx_request_returns_the_fragment_without_the_shell(client):
    build_row(Campaign)
    url = reverse(_name(Campaign, "changelist"))
    full = client.get(url).content.decode()
    fragment = client.get(url, headers={"HX-Request": "true"}).content.decode()
    assert "<html" in full
    assert "<html" not in fragment
    assert 'id="c-cl-results"' in fragment


@pytest.mark.django_db
def test_search_and_filter_links_keep_state_and_report_chips(client):
    build_row(Campaign)
    url = reverse(_name(Campaign, "changelist"))
    html = client.get(url, {"q": "zzz-no-such-campaign"}).content.decode()
    assert "Nothing matches your search or filters" in html
    assert "c-chip" in html
    assert "zzz-no-such-campaign" in html


@pytest.mark.django_db
def test_related_popup_is_a_dialog_fragment(client):
    url = reverse(_name(Brand, "add"))
    dialog = client.get(
        f"{url}?_popup=1", headers={"HX-Request": "true", "HX-Target": "console-dialog"}
    ).content.decode()
    assert "data-console-dialog-content" in dialog
    assert 'class="modal-content"' in dialog
    assert 'name="_popup"' in dialog
    assert "csrfmiddlewaretoken" in dialog
    page = client.get(f"{url}?_popup=1").content.decode()
    assert "data-console-dialog-content" not in page


@pytest.mark.django_db
def test_popup_save_answers_with_the_popup_response_marker(client):
    url = reverse(_name(Brand, "add"))
    response = client.post(
        f"{url}?_popup=1",
        {
            "name": "Popup Brand",
            "slug": "popup-brand",
            "_popup": "1",
            # The inline's management form, exactly as the rendered page posts it.
            "models-TOTAL_FORMS": "0",
            "models-INITIAL_FORMS": "0",
            "models-MIN_NUM_FORMS": "0",
            "models-MAX_NUM_FORMS": "1000",
        },
        headers={"HX-Request": "true", "HX-Target": "console-dialog"},
    )
    html = response.content.decode()
    assert response.status_code == 200, html[:400]
    assert "data-console-popup-response" in html
    assert Brand.objects.filter(name="Popup Brand").exists()


@pytest.mark.django_db
def test_delete_confirmation_needs_a_post_and_a_csrf_token(client):
    campaign = build_row(Campaign)
    url = reverse(_name(Campaign, "delete"), args=[campaign.pk])
    page = client.get(url).content.decode()
    assert "csrfmiddlewaretoken" in page
    assert Campaign.objects.filter(pk=campaign.pk).exists()
    guarded = Client(enforce_csrf_checks=True)
    guarded.force_login(get_user_model().objects.get(username="crud-render-admin"))
    assert guarded.post(url, {"post": "yes"}).status_code == 403
    assert Campaign.objects.filter(pk=campaign.pk).exists()
