"""Event bus delivery contract: isolation, retry, visibility, after-commit dispatch."""

from __future__ import annotations

from collections.abc import Iterator
from functools import partial
from typing import Any

import pytest
from django.db import DatabaseError, IntegrityError, OperationalError, transaction

from apps.core.events.bus import (
    MAX_HANDLER_RETRIES,
    DomainError,
    EventBus,
    event_bus,
    handler_name,
)
from apps.core.metrics.collector import Metric
from apps.core.utils.logging import bind_correlation_id, reset_correlation_id

EVENT = "contract.sample"


@pytest.fixture(autouse=True, name="clean_bus")
def isolated_bus() -> Iterator[EventBus]:
    event_bus.clear_handlers(EVENT)
    event_bus.reset_stats()
    yield event_bus
    event_bus.clear_handlers(EVENT)
    event_bus.reset_stats()


def test_the_bus_is_a_process_wide_singleton():
    assert EventBus() is event_bus


def test_subscribing_the_same_handler_twice_delivers_once(clean_bus):
    seen: list[Any] = []

    def handler(data):
        seen.append(data)

    clean_bus.subscribe(EVENT, handler)
    clean_bus.subscribe(EVENT, handler)

    clean_bus.publish(EVENT, 1)

    assert seen == [1]
    assert clean_bus.get_handler_count(EVENT) == 1


def test_bound_methods_and_partials_are_subscribable_and_named(clean_bus):
    seen: list[Any] = []

    def record(prefix, data):
        seen.append((prefix, data))

    clean_bus.subscribe(EVENT, seen.append)
    clean_bus.subscribe(EVENT, partial(record, "p"))

    clean_bus.publish(EVENT, "x")

    assert seen == ["x", ("p", "x")]
    assert handler_name(partial(record, "p")).endswith("record")
    assert handler_name(seen.append).endswith("append")


def test_a_failing_handler_never_blocks_the_others_and_is_recorded(clean_bus):
    seen: list[str] = []

    def broken(_data):
        raise DomainError("secret-token-123 refused")

    def healthy(_data):
        seen.append("ran")

    clean_bus.subscribe(EVENT, broken)
    clean_bus.subscribe(EVENT, healthy)

    clean_bus.publish(EVENT, {"email": "person@example.com"})

    assert seen == ["ran"]
    stats = clean_bus.stats()
    assert (stats["published"], stats["delivered"], stats["failed"]) == (1, 1, 1)
    (failure,) = clean_bus.recent_failures()
    assert failure.event_type == EVENT
    assert failure.handler.endswith("broken")
    assert failure.error_type == "DomainError"
    assert failure.attempts == 1
    # No payload and no exception message are kept: the record is PII-safe.
    assert "person@example.com" not in repr(failure)
    assert "secret-token-123" not in repr(failure)
    assert stats["failures_by_handler"] == {f"{EVENT}|{failure.handler}": 1}


def test_the_failure_record_carries_the_request_correlation_id(clean_bus):
    def broken(_data):
        raise ValueError("bad")

    clean_bus.subscribe(EVENT, broken)
    token = bind_correlation_id("req-12345678")
    try:
        clean_bus.publish(EVENT)
    finally:
        reset_correlation_id(token)

    assert clean_bus.recent_failures()[0].correlation_id == "req-12345678"


def test_unexpected_errors_still_surface_to_the_publisher(clean_bus):
    class Unexpected(Exception):
        pass

    def broken(_data):
        raise Unexpected("a programming error")

    clean_bus.subscribe(EVENT, broken)

    with pytest.raises(Unexpected):
        clean_bus.publish(EVENT)


@pytest.mark.django_db
def test_each_handler_runs_in_its_own_savepoint(clean_bus):
    """A database error in one handler undoes only its writes, never the caller's."""

    def half_done_then_fails(_data):
        Metric.objects.create(name="half-done", value=1)
        raise IntegrityError("constraint")

    def healthy(_data):
        Metric.objects.create(name="healthy", value=2)

    clean_bus.subscribe(EVENT, half_done_then_fails)
    clean_bus.subscribe(EVENT, healthy)

    with transaction.atomic():
        Metric.objects.create(name="publisher", value=0)
        clean_bus.publish(EVENT)
        # The publisher's own transaction is still usable and keeps its write.
        assert Metric.objects.filter(name="publisher").exists()

    assert set(Metric.objects.values_list("name", flat=True)) == {
        "publisher",
        "healthy",
    }
    assert clean_bus.stats()["failed"] == 1


def test_retries_are_bounded_and_only_for_transient_database_errors(clean_bus):
    calls = {"flaky": 0, "always": 0, "plain": 0, "undeclared": 0}

    def flaky(_data):
        calls["flaky"] += 1
        if calls["flaky"] < 3:
            raise OperationalError("deadlock detected")

    def always(_data):
        calls["always"] += 1
        raise OperationalError("still down")

    def plain(_data):
        calls["plain"] += 1
        raise ValueError("would fail the same way")

    def undeclared(_data):
        calls["undeclared"] += 1
        raise OperationalError("no retry declared")

    clean_bus.subscribe(EVENT, flaky, retries=2)
    clean_bus.subscribe(EVENT, always, retries=1)
    clean_bus.subscribe(EVENT, plain, retries=3)
    clean_bus.subscribe(EVENT, undeclared)

    clean_bus.publish(EVENT)

    assert calls == {"flaky": 3, "always": 2, "plain": 1, "undeclared": 1}
    attempts = {
        f.handler.rsplit(".", 1)[-1]: f.attempts for f in clean_bus.recent_failures()
    }
    assert attempts == {"always": 2, "plain": 1, "undeclared": 1}
    stats = clean_bus.stats()
    assert stats["retried"] == 3  # flaky twice, always once
    assert stats["delivered"] == 1
    assert stats["failed"] == 3


def test_the_retry_budget_is_capped():
    assert MAX_HANDLER_RETRIES == 3
    with pytest.raises(ValueError, match="retries"):
        event_bus.subscribe(EVENT, lambda _data: None, retries=MAX_HANDLER_RETRIES + 1)
    with pytest.raises(ValueError, match="retries"):
        event_bus.subscribe(EVENT, lambda _data: None, retries=-1)


def test_unsubscribe_forgets_the_retry_policy(clean_bus):
    calls: list[int] = []

    def flaky(_data):
        calls.append(1)
        raise OperationalError("down")

    clean_bus.subscribe(EVENT, flaky, retries=3)
    clean_bus.unsubscribe(EVENT, flaky)
    clean_bus.subscribe(EVENT, flaky)

    clean_bus.publish(EVENT)

    assert len(calls) == 1


def test_recent_failures_are_a_bounded_ring(clean_bus):
    def broken(_data):
        raise DatabaseError("x")

    clean_bus.subscribe(EVENT, broken)
    for _ in range(150):
        clean_bus.publish(EVENT)

    assert len(clean_bus.recent_failures()) == 100
    assert clean_bus.stats()["failed"] == 150


@pytest.mark.django_db
def test_publish_on_commit_waits_for_the_commit(
    clean_bus, django_capture_on_commit_callbacks
):
    seen: list[Any] = []
    clean_bus.subscribe(EVENT, seen.append)

    with (
        django_capture_on_commit_callbacks(execute=False) as callbacks,
        transaction.atomic(),
    ):
        clean_bus.publish_on_commit(EVENT, "after-commit")
        assert not seen

    assert not seen
    assert len(callbacks) == 1
    callbacks[0]()
    assert seen == ["after-commit"]


@pytest.mark.django_db(transaction=True)
def test_publish_on_commit_is_dropped_when_the_transaction_rolls_back(clean_bus):
    seen: list[Any] = []
    clean_bus.subscribe(EVENT, seen.append)

    with pytest.raises(RuntimeError), transaction.atomic():
        clean_bus.publish_on_commit(EVENT, "rolled-back")
        raise RuntimeError("abort")

    assert not seen

    with transaction.atomic():
        clean_bus.publish_on_commit(EVENT, "committed")

    assert seen == ["committed"]


@pytest.mark.django_db(transaction=True)
def test_publish_on_commit_delivers_immediately_outside_a_transaction(clean_bus):
    seen: list[Any] = []
    clean_bus.subscribe(EVENT, seen.append)

    clean_bus.publish_on_commit(EVENT, "now")

    assert seen == ["now"]
