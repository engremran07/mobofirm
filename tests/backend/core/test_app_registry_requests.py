"""Per-request memoisation and the single answer to "is app X on?"."""

from __future__ import annotations

import logging

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.core.app_service import (
    ALWAYS_ENABLED,
    AppService,
    app_enabled,
    current_registry,
    registry_flag_names,
)
from apps.core.models import AppRegistry
from apps.core.utils.request_cache import (
    forget,
    in_request_scope,
    memoize,
    request_scope,
)


def registry_queries(callable_):
    with CaptureQueriesContext(connection) as queries:
        callable_()
    return [q for q in queries if "core_appregistry" in q["sql"]]


def counting(calls: list[int]) -> int:
    calls.append(1)
    return len(calls)


def test_outside_a_request_scope_every_call_computes_afresh():
    calls: list[int] = []

    first = memoize("k", lambda: counting(calls))
    second = memoize("k", lambda: counting(calls))

    assert (first, second) == (1, 2)
    assert not in_request_scope()


def test_inside_a_scope_a_value_is_computed_once_and_forgettable():
    calls: list[int] = []

    def compute():
        calls.append(1)
        return len(calls)

    with request_scope():
        assert memoize("k", compute) == memoize("k", compute) == 1
        forget("k")
        assert memoize("k", compute) == 2

    assert memoize("k", compute) == 3


def test_scopes_do_not_leak_between_requests():
    with request_scope():
        memoize("k", lambda: "first")
    with request_scope():
        assert memoize("k", lambda: "second") == "second"


def test_a_nested_scope_reuses_the_outer_memo():
    with request_scope() as outer, request_scope() as inner:
        assert outer is inner


@pytest.mark.django_db
def test_the_registry_is_read_once_per_request_scope():
    AppRegistry.get_solo()

    def several_lookups():
        for label in ("blog", "shop", "wallet", "blog", "firmwares"):
            app_enabled(label)
        AppService.get("seo")

    outside = registry_queries(several_lookups)
    with request_scope():
        inside = registry_queries(several_lookups)

    assert len(outside) >= 5
    assert len(inside) == 1


@pytest.mark.django_db
def test_a_full_page_request_reads_the_registry_at_most_once():
    from django.test import Client
    from django.urls import reverse

    client = Client()
    client.get(reverse("home"))  # warm caches that are not under test

    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("home"))

    registry = [q for q in queries if "core_appregistry" in q["sql"]]
    assert response.status_code == 200
    assert len(registry) <= 1


@pytest.mark.django_db
def test_callers_get_their_own_copy_so_edits_never_leak_between_readers():
    with request_scope():
        first = AppRegistry.get_solo()
        first.blog_enabled = False  # an unsaved edit, e.g. an admin form

        second = AppRegistry.get_solo()

        assert second is not first
        assert second.blog_enabled is True
        assert app_enabled("blog") is True


@pytest.mark.django_db
def test_saving_the_registry_inside_a_request_is_visible_immediately():
    with request_scope():
        assert app_enabled("blog") is True
        registry = AppRegistry.get_solo()
        registry.blog_enabled = False
        registry.save()

        assert app_enabled("blog") is False
        assert current_registry().blog_enabled is False

        registry.blog_enabled = True
        registry.save()
        assert app_enabled("blog") is True


@pytest.mark.django_db
def test_a_disabled_app_resolves_to_nothing():
    registry = AppRegistry.get_solo()
    registry.referral_enabled = False
    registry.save()

    assert app_enabled("referral") is False
    assert AppService.get("referral") is None
    assert AppService.export("referral", "referral_code_for") is None


@pytest.mark.django_db
def test_an_unknown_label_is_off_and_logged_not_silently_on(caplog):
    with caplog.at_level(logging.ERROR, logger="apps.core.app_service"):
        assert app_enabled("blogg") is False
        assert AppService.get("no-such-app") is None
        assert app_enabled("") is False

    assert "no 'blogg_enabled' flag" in caplog.text


@pytest.mark.django_db
def test_device_identity_cannot_be_switched_off_by_the_generic_flag():
    registry = AppRegistry.get_solo()
    registry.devices_enabled = False
    registry.save()

    assert "devices" in ALWAYS_ENABLED
    assert app_enabled("devices") is True
    assert AppService.get("devices") is not None


def test_every_registry_flag_is_a_boolean_default_on_field():
    names = registry_flag_names()

    assert {"blog", "shop", "wallet", "devices", "device_identity"} <= names
    for field in AppRegistry._meta.fields:
        if field.name.endswith("_enabled"):
            assert field.get_internal_type() == "BooleanField"
            assert field.default is True
