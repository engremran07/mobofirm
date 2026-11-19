"""Core models (AppRegistry, SearchSettings, Metric), retention and the cleanup task."""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.http import HttpResponse
from django.template import engines
from django.template.response import SimpleTemplateResponse
from django.test import RequestFactory
from django.utils import timezone

from apps.core import admin_base
from apps.core.metrics.collector import Metric, MetricsCollector, Timer
from apps.core.models import AppRegistry, SearchSettings
from apps.core.tasks import (
    CLEANUP_SOFT_TIME_LIMIT,
    CLEANUP_TIME_LIMIT,
    cleanup_old_data,
)

pytestmark = pytest.mark.django_db


class RelatedAdmin(admin_base.RelatedOverviewMixin, admin.ModelAdmin):
    """The mixin under test, on a plain model admin."""


def test_app_registry_is_a_singleton_with_every_app_on_by_default():
    first = AppRegistry.get_solo()
    second = AppRegistry.get_solo()

    assert first.pk == second.pk
    assert AppRegistry.objects.count() == 1
    flags = [f for f in AppRegistry._meta.get_fields() if f.name.endswith("_enabled")]
    assert flags
    assert all(getattr(first, flag.name) is True for flag in flags)
    assert str(first) == "Application Registry"


def test_the_two_device_flags_explain_what_they_do():
    devices = AppRegistry._meta.get_field("devices_enabled")
    identity = AppRegistry._meta.get_field("device_identity_enabled")

    assert "never switched off" in devices.help_text
    assert "automatically" in identity.help_text


def test_search_settings_defaults_and_bounds():
    settings_row = SearchSettings.get_solo()
    assert (
        settings_row.popular_enabled,
        settings_row.popular_window_days,
        settings_row.popular_limit,
        settings_row.popular_cache_minutes,
    ) == (True, 30, 6, 10)
    assert str(settings_row) == "Search and popularity settings"

    for field, bad in (
        ("popular_window_days", 0),
        ("popular_window_days", 366),
        ("popular_limit", 0),
        ("popular_limit", 25),
        ("popular_cache_minutes", 0),
        ("popular_cache_minutes", 1441),
    ):
        setattr(settings_row, field, bad)
        with pytest.raises(ValidationError) as caught:
            settings_row.full_clean()
        assert field in caught.value.message_dict
        settings_row = SearchSettings.get_solo()


def test_metric_has_one_composite_index_and_no_redundant_name_index():
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(
            cursor, Metric._meta.db_table
        )
    indexed = sorted(
        tuple(info["columns"])
        for info in constraints.values()
        if info["index"] and not info["primary_key"]
    )

    assert indexed == [("name", "timestamp"), ("timestamp",)]
    assert [tuple(index.fields) for index in Metric._meta.indexes] == [
        ("name", "timestamp")
    ]
    assert Metric._meta.ordering == ["-timestamp"]


def test_metrics_are_stored_queried_and_timed():
    collector = MetricsCollector(backend="database")

    collector.increment("page_views", tags={"page": "home"})
    collector.decrement("page_views")
    collector.timing("db", 12.5)
    collector.gauge("queue", 3)
    with Timer("timed", tags={"k": "v"}):
        pass

    assert collector.get_metrics("page_views").count() == 2
    assert collector.get_metrics("page_views", tags={"page": "home"}).count() == 1
    assert collector.get_metrics("page_views", tags={"page": "other"}).count() == 0
    with pytest.raises(ValueError, match="Invalid metric tag name"):
        collector.get_metrics("page_views", tags={"a__b": 1})
    timed = collector.get_metrics("timed").get()
    assert timed.value >= 0
    assert str(Metric.objects.get(name="queue")).startswith("queue=3.0")


def test_a_failed_metric_write_does_not_poison_the_callers_transaction(monkeypatch):
    from django.db import DatabaseError, transaction

    def broken_create(*_args, **_kwargs):
        raise DatabaseError("metrics table unavailable")

    monkeypatch.setattr(Metric.objects, "create", broken_create)

    with transaction.atomic():
        MetricsCollector(backend="database").track("lost", 1)
        AppRegistry.get_solo()  # the surrounding transaction is still usable

    assert AppRegistry.objects.count() == 1


def test_unimplemented_backends_drop_samples_loudly_not_silently(caplog):
    collector = MetricsCollector(backend="statsd")

    with caplog.at_level("WARNING"):
        collector.track("x", 1)

    assert "not implemented" in caplog.text
    assert Metric.objects.count() == 0
    assert not list(collector.get_metrics("x"))


def test_cleanup_removes_only_metric_samples_older_than_the_window():
    old = Metric.objects.create(name="old", value=1)
    fresh = Metric.objects.create(name="fresh", value=1)
    Metric.objects.filter(pk=old.pk).update(
        timestamp=timezone.now() - timedelta(days=120)
    )

    call_command("cleanup_old_data", "--days", "90", "--dry-run", stdout=StringIO())
    assert Metric.objects.count() == 2

    out = StringIO()
    call_command("cleanup_old_data", "--days", "90", stdout=out)

    assert list(Metric.objects.values_list("pk", flat=True)) == [fresh.pk]
    assert "Metric samples: 1" in out.getvalue()

    call_command("cleanup_old_data", "--days", "90", stdout=StringIO())  # idempotent
    assert Metric.objects.count() == 1


def test_the_cleanup_task_has_time_limits_and_is_safe_to_redeliver():
    assert cleanup_old_data.soft_time_limit == CLEANUP_SOFT_TIME_LIMIT
    assert cleanup_old_data.time_limit == CLEANUP_TIME_LIMIT
    assert 0 < CLEANUP_SOFT_TIME_LIMIT < CLEANUP_TIME_LIMIT
    assert cleanup_old_data.acks_late is True


def test_the_cleanup_task_runs_the_retention_command():
    Metric.objects.create(name="stale", value=1)
    Metric.objects.update(timestamp=timezone.now() - timedelta(days=400))

    cleanup_old_data.apply(kwargs={"days": 30}).get()

    assert Metric.objects.count() == 0


def test_related_overview_forgets_the_request_when_the_form_view_raises(monkeypatch):
    seen: list[object] = []

    def exploding_view(*_args, **_kwargs):
        seen.append(admin_base.active_request())
        raise RuntimeError("form exploded")

    monkeypatch.setattr(admin.ModelAdmin, "changeform_view", exploding_view)
    request = RequestFactory().get("/")
    model_admin = RelatedAdmin(SearchSettings, admin.site)

    with pytest.raises(RuntimeError, match="form exploded"):
        model_admin.changeform_view(request)

    assert seen == [request]  # available while the view ran
    assert admin_base.active_request() is None  # and released afterwards


def test_related_overview_keeps_the_request_until_a_lazy_response_is_rendered(
    monkeypatch,
):
    response = SimpleTemplateResponse(engines["django"].from_string("ok"))
    monkeypatch.setattr(
        admin.ModelAdmin, "changeform_view", lambda *_args, **_kwargs: response
    )
    request = RequestFactory().get("/")
    model_admin = RelatedAdmin(SearchSettings, admin.site)

    assert model_admin.changeform_view(request) is response
    assert admin_base.active_request() is request

    response.render()

    assert admin_base.active_request() is None


def test_related_overview_releases_the_request_for_an_already_rendered_response(
    monkeypatch,
):
    rendered = HttpResponse("done")
    monkeypatch.setattr(
        admin.ModelAdmin, "changeform_view", lambda *_args, **_kwargs: rendered
    )
    model_admin = RelatedAdmin(SearchSettings, admin.site)

    assert model_admin.changeform_view(RequestFactory().get("/")) is rendered
    assert admin_base.active_request() is None
