"""Correlation ids: bound per request, stamped on log records, forwarded to Celery."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest
from celery import signals
from django.http import HttpResponse
from django.test import RequestFactory

from apps.core import celery_context
from apps.core.middleware.correlation import CorrelationIdMiddleware
from apps.core.utils.logging import (
    NO_CORRELATION_ID,
    JsonFormatter,
    bind_correlation_id,
    get_correlation_id,
    install_log_record_factory,
    log_event,
    normalize_correlation_id,
    reset_correlation_id,
)
from apps.core.utils.request_cache import in_request_scope

logger = logging.getLogger("tests.core.correlation")


def run_middleware(view, **headers):
    request = RequestFactory().get("/", **headers)
    return request, CorrelationIdMiddleware(view)(request)


def test_the_id_is_bound_for_the_whole_request_and_released_after(caplog):
    seen: dict[str, object] = {}

    def view(request):
        seen["bound"] = get_correlation_id()
        seen["scope"] = in_request_scope()
        logger.warning("inside the request")
        return HttpResponse("ok")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        request, response = run_middleware(view, HTTP_X_REQUEST_ID="abc-12345")

    assert seen == {"bound": "abc-12345", "scope": True}
    assert request.correlation_id == "abc-12345"
    assert response["X-Request-ID"] == "abc-12345"
    assert [r.correlation_id for r in caplog.records] == ["abc-12345"]
    assert get_correlation_id() == ""
    assert not in_request_scope()


def test_an_unsafe_inbound_id_is_replaced_by_a_generated_one():
    request, response = run_middleware(
        lambda _request: HttpResponse("ok"), HTTP_X_REQUEST_ID="bad id\r\nINJECT"
    )

    assert "INJECT" not in request.correlation_id
    assert normalize_correlation_id(request.correlation_id) == request.correlation_id
    assert response["X-Request-ID"] == request.correlation_id


def test_the_context_is_released_even_when_the_view_raises():
    def view(_request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run_middleware(view)

    assert get_correlation_id() == ""
    assert not in_request_scope()


def test_records_outside_a_request_show_a_placeholder(caplog):
    with caplog.at_level(logging.WARNING, logger=logger.name):
        logger.warning("no request here")

    assert caplog.records[0].correlation_id == NO_CORRELATION_ID


def test_installing_the_record_factory_twice_does_not_stack_wrappers():
    install_log_record_factory()
    first = logging.getLogRecordFactory()

    install_log_record_factory()

    assert logging.getLogRecordFactory() is first


def test_json_lines_carry_the_id_the_event_payload_and_no_stray_extras():
    handler_records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(self.format(record))

    capture = Capture()
    capture.setFormatter(JsonFormatter())
    json_logger = logging.getLogger("tests.core.correlation.json")
    json_logger.addHandler(capture)
    json_logger.setLevel(logging.INFO)
    json_logger.propagate = False
    token = bind_correlation_id("req-abcdef12")
    try:
        log_event(json_logger, "info", "did a thing", count=2)
        json_logger.warning("with extra", extra={"email": "person@example.com"})
        try:
            raise ValueError("kaboom")
        except ValueError:
            json_logger.exception("failed")
    finally:
        reset_correlation_id(token)
        json_logger.removeHandler(capture)

    first, second, third = (json.loads(line) for line in handler_records)
    assert first["correlation_id"] == "req-abcdef12"
    assert first["event"] == {"count": 2}
    assert first["message"] == "did a thing"
    assert first["level"] == "INFO"
    assert "person@example.com" not in handler_records[1]
    assert "email" not in second
    assert "ValueError: kaboom" in third["exception"]


def test_the_current_id_is_copied_into_celery_message_headers():
    headers: dict[str, str] = {}
    token = bind_correlation_id("req-abcdef12")
    try:
        celery_context.inject_correlation_header(headers=headers)
    finally:
        reset_correlation_id(token)

    assert headers == {celery_context.CORRELATION_HEADER: "req-abcdef12"}


def test_nothing_is_added_to_headers_outside_a_request():
    headers: dict[str, str] = {}

    celery_context.inject_correlation_header(headers=headers)
    celery_context.inject_correlation_header(headers=None)

    assert not headers


def task_with_header(value):
    return SimpleNamespace(request=SimpleNamespace(x_request_id=value))


def test_a_worker_binds_the_id_the_task_was_queued_with_then_restores():
    task = task_with_header("req-abcdef12")

    celery_context.bind_task_correlation(task_id="11111111-aaaa", task=task)
    bound = get_correlation_id()
    celery_context.release_task_correlation(task_id="11111111-aaaa")

    assert bound == "req-abcdef12"
    assert get_correlation_id() == ""


def test_a_task_without_a_valid_header_falls_back_to_its_task_id():
    for value in (None, "bad id\nINJECT"):
        celery_context.bind_task_correlation(
            task_id="22222222-bbbb", task=task_with_header(value)
        )
        bound = get_correlation_id()
        celery_context.release_task_correlation(task_id="22222222-bbbb")

        assert bound == "22222222-bbbb"
        assert get_correlation_id() == ""


def test_eager_execution_inside_a_request_keeps_the_request_id():
    token = bind_correlation_id("req-abcdef12")
    try:
        celery_context.bind_task_correlation(
            task_id="33333333-cccc", task=task_with_header(None)
        )
        bound = get_correlation_id()
        celery_context.release_task_correlation(task_id="33333333-cccc")
        restored = get_correlation_id()
    finally:
        reset_correlation_id(token)

    assert bound == "req-abcdef12"
    assert restored == "req-abcdef12"


def test_the_receivers_are_connected_to_celery_signals_exactly_once():
    celery_context.connect()
    celery_context.connect()

    for signal in (
        signals.before_task_publish,
        signals.task_prerun,
        signals.task_postrun,
    ):
        ours = [
            lookup
            for lookup, _receiver in signal.receivers
            if lookup[0] == "apps.core.celery_context"
        ]
        assert len(ours) == 1
