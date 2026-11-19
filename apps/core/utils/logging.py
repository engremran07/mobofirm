"""Logging helpers: correlation id context, record stamping, JSON formatter.

The correlation id is a ``contextvars`` value bound by ``CorrelationIdMiddleware`` for
a request and by ``apps.core.celery_context`` for a task. ``install_log_record_factory``
(called from ``CoreConfig.ready``) stamps it on every log record so any formatter can
print ``%(correlation_id)s``; ``JsonFormatter`` emits one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# A correlation id is only ever a short, log-safe token; anything else (an inbound
# header, a Celery header) is replaced so a client can never inject log content.
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

# Shown by formatters when a record is emitted outside a request or a task.
NO_CORRELATION_ID = "-"

_correlation_id: ContextVar[str] = ContextVar("core_correlation_id", default="")


def normalize_correlation_id(value: object) -> str | None:
    """Return ``value`` when it is a log-safe correlation id, otherwise ``None``."""
    if isinstance(value, str) and CORRELATION_ID_PATTERN.fullmatch(value):
        return value
    return None


def get_correlation_id() -> str:
    """The id of the request or task being handled, ``""`` outside of both."""
    return _correlation_id.get()


def bind_correlation_id(value: str) -> Token[str]:
    """Make ``value`` the current id; pass the token to :func:`reset_correlation_id`."""
    return _correlation_id.set(value)


def reset_correlation_id(token: Token[str]) -> None:
    """Restore the id current before the matching :func:`bind_correlation_id`."""
    _correlation_id.reset(token)


class _CorrelationRecordFactory:
    """Log record factory that stamps ``record.correlation_id`` on every record."""

    def __init__(self, previous: Any) -> None:
        self.previous = previous

    def __call__(self, *args: Any, **kwargs: Any) -> logging.LogRecord:
        record: logging.LogRecord = self.previous(*args, **kwargs)
        record.correlation_id = get_correlation_id() or NO_CORRELATION_ID
        return record


def install_log_record_factory() -> None:
    """Stamp ``record.correlation_id`` on every log record of the process (idempotent).

    Done on the record factory rather than on one handler so that every logger,
    handler and formatter (including third-party ones and ``LOGGING`` configurations
    that know nothing about this module) can use ``%(correlation_id)s``.
    """
    current = logging.getLogRecordFactory()
    if not isinstance(current, _CorrelationRecordFactory):
        logging.setLogRecordFactory(_CorrelationRecordFactory(current))


class CorrelationIdFilter(logging.Filter):
    """Logging filter for handlers: guarantees ``record.correlation_id`` exists."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "correlation_id", ""):
            record.correlation_id = get_correlation_id() or NO_CORRELATION_ID
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: level, logger, message, correlation id, ``event``.

    Only these fields are emitted; arbitrary ``extra`` attributes (which may carry
    personal data) are not serialised. ``event`` is the structured payload written by
    :func:`log_event`, whose callers are responsible for keeping it free of PII.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "")
            or get_correlation_id()
            or NO_CORRELATION_ID,
        }
        event = getattr(record, "event", None)
        if event is not None:
            payload["event"] = event
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=True)


def get_logger(name: str) -> logging.Logger:
    """
    Centralized logger factory to keep a consistent format across apps.
    """
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: str, message: str, **extra: Any) -> None:
    """
    Structured logging helper. Adds 'event' payload via `extra`.
    """
    if level == "debug":
        logger.debug(message, extra={"event": extra})
    elif level == "info":
        logger.info(message, extra={"event": extra})
    elif level == "warning":
        logger.warning(message, extra={"event": extra})
    elif level == "error":
        logger.error(message, extra={"event": extra})
    else:
        logger.log(logging.INFO, message, extra={"event": extra})
