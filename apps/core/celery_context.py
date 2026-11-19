"""Carry the request correlation id from the web process into Celery tasks.

The publisher side copies the current id into the task message headers
(``before_task_publish``); the worker side binds it for the duration of the task
(``task_prerun`` / ``task_postrun``) so the task's log records carry the id of the
request that queued it. A task queued outside a request (beat, a management command)
gets its own task id as correlation id on the worker.

Only the opaque, validated id travels; it is never derived from user data.
"""

from __future__ import annotations

from contextvars import Token
from typing import Any

from celery import signals

from apps.core.utils.logging import (
    bind_correlation_id,
    get_correlation_id,
    normalize_correlation_id,
    reset_correlation_id,
)

# Message header carrying the id. ``correlation_id`` itself is a reserved AMQP
# message property in Celery, hence the prefixed name.
CORRELATION_HEADER = "x_request_id"

_DISPATCH_UID = "apps.core.celery_context"
_tokens: dict[str, Token[str]] = {}


def inject_correlation_header(
    headers: dict[str, Any] | None = None, **_kwargs: Any
) -> None:
    """``before_task_publish``: add the current request id to the task headers."""
    correlation_id = get_correlation_id()
    if correlation_id and headers is not None:
        headers.setdefault(CORRELATION_HEADER, correlation_id)


def bind_task_correlation(
    task_id: str | None = None, task: Any = None, **_kwargs: Any
) -> None:
    """``task_prerun``: bind the id the task was queued with (or the task id)."""
    request = getattr(task, "request", None)
    correlation_id = (
        normalize_correlation_id(getattr(request, CORRELATION_HEADER, None))
        or get_correlation_id()  # eager execution inside a request keeps its id
        or normalize_correlation_id(task_id)
    )
    if correlation_id and task_id:
        _tokens[task_id] = bind_correlation_id(correlation_id)


def release_task_correlation(task_id: str | None = None, **_kwargs: Any) -> None:
    """``task_postrun``: restore whatever was bound before the task started."""
    token = _tokens.pop(task_id, None) if task_id else None
    if token is not None:
        reset_correlation_id(token)


def connect() -> None:
    """Connect the receivers (idempotent: ``dispatch_uid`` de-duplicates)."""
    signals.before_task_publish.connect(
        inject_correlation_header, dispatch_uid=_DISPATCH_UID
    )
    signals.task_prerun.connect(bind_task_correlation, dispatch_uid=_DISPATCH_UID)
    signals.task_postrun.connect(release_task_correlation, dispatch_uid=_DISPATCH_UID)
