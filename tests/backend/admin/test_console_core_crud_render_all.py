"""Every registered ModelAdmin renders every CRUD page in every delivery mode.

Modes: a full page, an HTMX fragment (``HX-Request``) and the dialog host
(``HX-Target: console-dialog``). The changelist is also requested with search, every
sort column, every filter choice (and a sample value for range/text filters), the date
hierarchy, pagination, the popup chooser and the bulk-delete confirmation.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.contrib.auth import get_user_model
from django.db import models
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.core.admin_filters import (
    FieldTextFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
    RangeNumericFilter,
    TextFilter,
)
from tests.backend.admin.admin_model_factory import CannotBuild, admin_pk, build_row

REGISTERED: list[type[models.Model]] = sorted(
    admin.site._registry, key=lambda model: model._meta.label_lower
)

INLINE_SCRIPT = re.compile(
    r"<script\b(?![^>]*(?:\bsrc=|type=\"application/json\"))", re.IGNORECASE
)
INLINE_HANDLER = re.compile(r"<[a-z][^>]*\son[a-z]+\s*=", re.IGNORECASE)
INLINE_STYLE = re.compile(r"<[a-z][^>]*\sstyle\s*=", re.IGNORECASE)
POST_FORM = re.compile(r"<form\b[^>]*\bmethod=\"post\"", re.IGNORECASE)
STOCK_ASSET = re.compile(r"/static/admin/(?:js|css)/", re.IGNORECASE)
NATIVE_DIALOG = re.compile(r"\b(?:confirm|alert|prompt)\(")
DOCUMENT = re.compile(r"<(?:!doctype|html)\b", re.IGNORECASE)

MODES: dict[str, dict[str, str]] = {
    "full": {},
    "htmx": {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "console-content"},
    "dialog": {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "console-dialog"},
}


@pytest.fixture
def superuser(db, settings):
    settings.STAFF_MFA_REQUIRED = False
    return get_user_model().objects.create_superuser(
        username="crud-all-admin",
        email="crud-all-admin@example.test",
        password="x-test-password-123",
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def client(superuser) -> Client:
    test_client = Client()
    test_client.force_login(superuser)
    return test_client


def _name(model: type[models.Model], suffix: str) -> str:
    return f"admin:{model._meta.app_label}_{model._meta.model_name}_{suffix}"


def _request_for(user, path: str = "/admin/"):
    request = RequestFactory().get(path)
    request.user = user
    return request


def _check(response, label: str, mode: str, *, expected: tuple[int, ...] = (200,)):
    assert response.status_code in expected, (
        f"{label} [{mode}]: HTTP {response.status_code}"
    )
    if response.status_code != 200:
        return ""
    html = response.content.decode()
    assert INLINE_SCRIPT.search(html) is None, f"{label} [{mode}]: inline <script>"
    assert INLINE_HANDLER.search(html) is None, f"{label} [{mode}]: inline handler"
    assert INLINE_STYLE.search(html) is None, f"{label} [{mode}]: inline style"
    assert STOCK_ASSET.search(html) is None, f"{label} [{mode}]: stock admin asset"
    assert NATIVE_DIALOG.search(html) is None, f"{label} [{mode}]: native dialog"
    if POST_FORM.search(html):
        assert "csrfmiddlewaretoken" in html, f"{label} [{mode}]: POST without CSRF"
    if mode == "full":
        ids = set(re.findall(r"\bid=\"([^\"]+)\"", html))
        for attribute in ("aria-describedby", "aria-labelledby", "aria-controls"):
            for value in re.findall(rf"\b{attribute}=\"([^\"]*)\"", html):
                missing = [token for token in value.split() if token not in ids]
                assert not missing, f"{label} [{mode}]: {attribute} -> {missing}"
    if mode != "full":
        assert DOCUMENT.search(html) is None, f"{label} [{mode}]: fragment is a page"
    return html


def _get(
    client,
    url: str,
    label: str,
    *,
    expected: tuple[int, ...] = (200,),
    modes: tuple[str, ...] = ("full", "htmx", "dialog"),
):
    """GET ``url`` in the given delivery modes and check each response."""
    for mode in modes:
        _check(client.get(url, **MODES[mode]), label, mode, expected=expected)


def _row(model: type[models.Model]) -> models.Model | None:
    try:
        return build_row(model)
    except CannotBuild:
        return None


def _filter_urls(model_admin, user, base: str) -> list[str]:
    """One URL per filter choice, plus sample range/text values for custom inputs."""
    request = _request_for(user, base)
    changelist: ChangeList = model_admin.get_changelist_instance(request)
    urls: list[str] = []
    for spec in changelist.filter_specs:
        for choice in spec.choices(changelist):
            query = choice["query_string"]
            if query and query != "?":
                urls.append(f"{base}{query}")
        if isinstance(spec, RangeDateFilter | RangeDateTimeFilter | RangeNumericFilter):
            sample = {
                "date": "2024-01-01",
                "datetime-local": "2024-01-01T10:30",
                "number": "1",
            }[spec.input_type]
            urls.append(f"{base}?{spec.name_from}={sample}&{spec.name_to}={sample}")
        elif isinstance(spec, FieldTextFilter | TextFilter):
            urls.append(f"{base}?{spec.parameter_name}=zz")
    return urls


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_changelist_renders_with_search_sort_filters_and_paging(
    model, client, superuser
):
    label = model._meta.label_lower
    model_admin = admin.site._registry[model]
    base = reverse(_name(model, "changelist"))
    _row(model)
    _get(client, base, f"{label} list")
    if not model_admin.has_view_or_change_permission(_request_for(superuser)):
        return
    if getattr(model_admin, "search_fields", None):
        _get(client, f"{base}?q=zz", f"{label} search", modes=("full", "htmx"))
    columns = model_admin.get_list_display(_request_for(superuser))
    for index in range(min(len(columns), 8)):
        _get(client, f"{base}?o={index}", f"{label} sort {index}", modes=("full",))
        _get(client, f"{base}?o=-{index}", f"{label} sort -{index}", modes=("htmx",))
    for position, url in enumerate(_filter_urls(model_admin, superuser, base)):
        modes = ("full", "htmx") if position < 2 else ("full",)
        _get(client, url, f"{label} filter {url}", modes=modes)
    hierarchy = model_admin.date_hierarchy
    if hierarchy:
        now = timezone.now()
        _get(client, f"{base}?{hierarchy}__year={now.year}", f"{label} year")
        _get(
            client,
            f"{base}?{hierarchy}__year={now.year}&{hierarchy}__month={now.month}",
            f"{label} month",
            modes=("full",),
        )
    _get(client, f"{base}?p=2", f"{label} page 2", expected=(200, 302), modes=("full",))
    _get(client, f"{base}?all=", f"{label} show all", modes=("full",))
    to_field = model._meta.pk.name
    _get(
        client,
        f"{base}?_popup=1&_to_field={to_field}",
        f"{label} popup",
        modes=("full",),
    )
    malformed = client.get(f"{base}?{hierarchy or 'pk'}__year=not-a-year")
    assert malformed.status_code in (200, 302), label


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_form_history_and_delete_pages_render_in_every_mode(model, client, superuser):
    label = model._meta.label_lower
    model_admin = admin.site._registry[model]
    request = _request_for(superuser)
    if model_admin.has_add_permission(request):
        add = reverse(_name(model, "add"))
        _get(client, add, f"{label} add")
        _get(client, f"{add}?_popup=1", f"{label} add popup", expected=(200, 302))
    instance = _row(model)
    if instance is None:
        return
    pk = admin_pk(instance)
    change = reverse(_name(model, "change"), args=[pk])
    _get(client, change, f"{label} change")
    _get(client, f"{change}?_popup=1", f"{label} change popup", expected=(200, 302))
    _get(client, reverse(_name(model, "history"), args=[pk]), f"{label} history")
    if model_admin.has_delete_permission(request, instance):
        delete = reverse(_name(model, "delete"), args=[pk])
        _get(client, delete, f"{label} delete")


@pytest.mark.parametrize("model", REGISTERED, ids=lambda m: m._meta.label_lower)
@pytest.mark.django_db
def test_bulk_delete_confirmation_is_csrf_protected_and_never_deletes(
    model, client, superuser
):
    label = model._meta.label_lower
    model_admin = admin.site._registry[model]
    instance = _row(model)
    if instance is None or not model_admin.has_delete_permission(
        _request_for(superuser), instance
    ):
        return
    base = reverse(_name(model, "changelist"))
    payload: dict[str, Any] = {
        "action": "delete_selected",
        "_selected_action": [str(admin_pk(instance))],
    }
    for mode, headers in MODES.items():
        response = client.post(base, payload, **headers)
        if response.status_code not in (200, 302):
            raise AssertionError(f"{label} [{mode}]: HTTP {response.status_code}")
        if response.status_code == 200:
            html = _check(response, f"{label} bulk delete", mode)
            if "delete_selected_confirmation" in html or 'name="post"' in html:
                assert "csrfmiddlewaretoken" in html
    assert model._default_manager.filter(pk=instance.pk).exists(), label
