"""
Event Bus - Internal Event System
==================================

Decoupled, in-process event system for app-to-app communication.

Delivery contract
-----------------
* **Synchronous, in-process, at most once.** ``publish`` calls every subscriber in the
  publisher's thread. Nothing is persisted: if the process dies mid-delivery, or a
  handler exhausts its attempts, that delivery is *not* replayed. Money-like workflows
  must therefore make their handlers idempotent (reward/escrow keys) and own a
  reconcile path; the bus only guarantees visibility of the loss (below).
* **Publish after commit.** A publisher that has just written state must use
  ``publish_on_commit`` (or ``transaction.on_commit``) so subscribers never observe
  data that is later rolled back, and never run inside the publisher's transaction.
* **Per-handler isolation.** When a transaction is open each handler runs in its own
  savepoint, so a failing handler undoes only its own writes and can never leave the
  connection in an aborted state for the next handler or for the publisher. A failing
  handler never blocks the remaining subscribers or the publisher.
* **Bounded retry, only where declared.** ``subscribe(event, handler, retries=n)``
  retries transient database errors (``RETRYABLE_ERRORS``: deadlocks, serialisation
  failures) at most ``n`` extra times (``n <= MAX_HANDLER_RETRIES``), each attempt in a
  fresh savepoint and without sleeping in the request thread. Every other failure is
  terminal on the first attempt. Retrying handlers must be idempotent.
* **Visible failures.** Every terminal failure is logged with the event, the handler
  and the request correlation id, counted (``stats()``) and kept in a bounded ring
  (``recent_failures()``). Payloads and exception messages are never stored in the
  record, so it is safe for personal data. Exceptions outside ``HANDLER_ERRORS`` are
  programming errors and propagate to the publisher.
* **Idempotent subscription.** Subscribing the same handler to the same event twice
  is a no-op, so repeated ``AppConfig.ready`` calls cannot double-deliver.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, ClassVar

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DatabaseError, OperationalError, transaction

from apps.core.utils.logging import get_correlation_id

logger = logging.getLogger(__name__)

MAX_HANDLER_RETRIES = 3
RECENT_FAILURE_LIMIT = 100


class DomainError(Exception):
    """Base for business errors that an event subscriber may legitimately raise."""


# Failures a subscriber may raise; they are logged so one bad handler never blocks
# the publisher or the remaining subscribers.
HANDLER_ERRORS: tuple[type[Exception], ...] = (
    DomainError,
    DatabaseError,
    ObjectDoesNotExist,
    ValidationError,
    ArithmeticError,
    AttributeError,
    ImportError,
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)

# The only failures worth repeating: transient database conflicts (deadlock,
# serialisation failure, dropped connection). Anything else would fail the same way.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (OperationalError,)


@dataclass(frozen=True, slots=True)
class DeliveryFailure:
    """A terminal handler failure. Holds no payload and no exception message."""

    event_type: str
    handler: str
    error_type: str
    attempts: int
    correlation_id: str
    occurred_at: datetime


def handler_name(handler: Callable[..., Any]) -> str:
    """A stable, readable name for ``handler`` (functions, methods, partials)."""
    target = getattr(handler, "func", handler)  # functools.partial
    module = getattr(target, "__module__", "") or ""
    name = getattr(target, "__qualname__", "") or type(target).__name__
    return f"{module}.{name}" if module else name


def _savepoint() -> AbstractContextManager[Any]:
    """A savepoint when a transaction is open; autocommit needs no isolation."""
    if transaction.get_connection().in_atomic_block:
        return transaction.atomic()
    return nullcontext()


class EventBus:
    """
    Internal event bus for decoupled app communication.

    Usage:
        # Publishing events (after the publisher's transaction committed)
        event_bus.publish_on_commit('blog.post_published', {'post_id': 123})

        # Subscribing to events
        @event_bus.subscribe('blog.post_published')
        def on_post_published(data):
            logger.info(f"Post {data['post_id']} was published!")

        # A handler that must survive a transient database conflict
        @event_bus.subscribe('wallet.credited', retries=2)
        def on_credit(data): ...
    """

    _instance = None
    _handlers: ClassVar[dict[str, list[Callable]]] = {}
    _retries: ClassVar[dict[str, list[tuple[Callable, int]]]] = {}
    _counters: ClassVar[dict[str, int]] = {}
    _failures_by_handler: ClassVar[dict[str, int]] = {}
    _recent_failures: ClassVar[deque[DeliveryFailure]] = deque(
        maxlen=RECENT_FAILURE_LIMIT
    )
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def subscribe(
        self, event_type: str, handler: Callable | None = None, *, retries: int = 0
    ):
        """
        Subscribe to an event (idempotent per ``(event_type, handler)``).

        Can be used as decorator:
            @event_bus.subscribe('event.name')
            def handler(data):
                pass

        Or as method:
            event_bus.subscribe('event.name', handler_function)

        Args:
            event_type: Event identifier (e.g., 'blog.post_published')
            handler: Handler function (if not used as decorator)
            retries: Extra attempts after a transient database error (0..3). The
                handler must be idempotent.
        """
        if not 0 <= retries <= MAX_HANDLER_RETRIES:
            raise ValueError(f"retries must be between 0 and {MAX_HANDLER_RETRIES}.")

        def decorator(func: Callable):
            handlers = self._handlers.setdefault(event_type, [])
            if func in handlers:
                logger.debug(
                    "%s is already subscribed to %s", handler_name(func), event_type
                )
            else:
                handlers.append(func)
                logger.debug("Subscribed %s to %s", handler_name(func), event_type)
            self._set_retries(event_type, func, retries)
            return func

        if handler is not None:
            return decorator(handler)
        return decorator

    def unsubscribe(self, event_type: str, handler: Callable):
        """
        Unsubscribe a handler from an event.

        Args:
            event_type: Event identifier
            handler: Handler function to remove
        """
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
                logger.debug(
                    "Unsubscribed %s from %s", handler_name(handler), event_type
                )
            except ValueError:
                pass
        self._set_retries(event_type, handler, 0)

    def clear_handlers(self, event_type: str | None = None):
        """
        Clear all handlers for an event type, or all handlers.

        Args:
            event_type: Event identifier (clears all if None)
        """
        if event_type:
            self._handlers.pop(event_type, None)
            self._retries.pop(event_type, None)
            logger.debug("Cleared handlers for %s", event_type)
        else:
            self._handlers.clear()
            self._retries.clear()
            logger.debug("Cleared all event handlers")

    def get_event_types(self) -> list[str]:
        """Get all registered event types"""
        return list(self._handlers.keys())

    def get_handler_count(self, event_type: str) -> int:
        """Get number of handlers for an event type"""
        return len(self._handlers.get(event_type, []))

    def _set_retries(self, event_type: str, handler: Callable, retries: int) -> None:
        entries = [
            entry for entry in self._retries.get(event_type, []) if entry[0] != handler
        ]
        if retries:
            entries.append((handler, retries))
        if entries:
            self._retries[event_type] = entries
        else:
            self._retries.pop(event_type, None)

    def _retries_for(self, event_type: str, handler: Callable) -> int:
        for candidate, retries in self._retries.get(event_type, []):
            if candidate == handler:
                return retries
        return 0

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------
    def publish(self, event_type: str, data: Any = None):
        """
        Publish an event to all subscribers, now, in the caller's thread.

        Call it after the publisher's transaction committed (see
        ``publish_on_commit``). Handler failures are isolated, retried when declared,
        recorded and counted; they never reach the caller (see the module docstring).

        Args:
            event_type: Event identifier
            data: Event data (dictionary, object, etc.)
        """
        # A snapshot: a handler may subscribe or unsubscribe while we deliver.
        handlers = list(self._handlers.get(event_type, []))
        self._count("published")

        if not handlers:
            logger.debug("No handlers for event '%s'", event_type)
            return

        logger.info("Publishing event '%s' to %s handlers", event_type, len(handlers))

        for handler in handlers:
            self._deliver(event_type, handler, data)

    def publish_on_commit(
        self, event_type: str, data: Any = None, *, using: str | None = None
    ) -> None:
        """Publish once the surrounding transaction commits (now, outside one).

        The delivery is dropped when the transaction rolls back, so subscribers never
        react to state that does not exist.
        """
        transaction.on_commit(partial(self.publish, event_type, data), using=using)

    def _deliver(self, event_type: str, handler: Callable, data: Any) -> None:
        allowed = 1 + self._retries_for(event_type, handler)
        attempt = 0
        while True:
            attempt += 1
            try:
                with _savepoint():
                    handler(data)
            except RETRYABLE_ERRORS as exc:
                if attempt < allowed:
                    self._count("retried")
                    logger.warning(
                        "Event handler %s hit a transient error for %s "
                        "(attempt %s of %s); retrying",
                        handler_name(handler),
                        event_type,
                        attempt,
                        allowed,
                    )
                    continue
                self._record_failure(event_type, handler, exc, attempt)
                return
            except HANDLER_ERRORS as exc:
                self._record_failure(event_type, handler, exc, attempt)
                return
            self._count("delivered")
            return

    # ------------------------------------------------------------------
    # Failure visibility
    # ------------------------------------------------------------------
    def _record_failure(
        self, event_type: str, handler: Callable, exc: Exception, attempts: int
    ) -> None:
        name = handler_name(handler)
        failure = DeliveryFailure(
            event_type=event_type,
            handler=name,
            error_type=type(exc).__name__,
            attempts=attempts,
            correlation_id=get_correlation_id(),
            occurred_at=datetime.now(UTC),
        )
        with self._lock:
            self._counters["failed"] = self._counters.get("failed", 0) + 1
            key = f"{event_type}|{name}"
            self._failures_by_handler[key] = self._failures_by_handler.get(key, 0) + 1
            self._recent_failures.append(failure)
        logger.error(
            "Event handler %s failed for %s after %s attempt(s): %s",
            name,
            event_type,
            attempts,
            failure.error_type,
            exc_info=exc,
            extra={
                "event": {
                    "name": "event_handler_failed",
                    "event_type": event_type,
                    "handler": name,
                    "error_type": failure.error_type,
                    "attempts": attempts,
                }
            },
        )

    def _count(self, counter: str) -> None:
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + 1

    def stats(self) -> dict[str, Any]:
        """Process-local delivery counters (for an operator view or a metrics sink)."""
        with self._lock:
            return {
                "published": self._counters.get("published", 0),
                "delivered": self._counters.get("delivered", 0),
                "retried": self._counters.get("retried", 0),
                "failed": self._counters.get("failed", 0),
                "failures_by_handler": dict(self._failures_by_handler),
            }

    def recent_failures(self) -> list[DeliveryFailure]:
        """The latest terminal failures, oldest first (bounded ring, no payloads)."""
        with self._lock:
            return list(self._recent_failures)

    def reset_stats(self) -> None:
        """Forget counters and recent failures (tests, operator reset)."""
        with self._lock:
            self._counters.clear()
            self._failures_by_handler.clear()
            self._recent_failures.clear()


# Global event bus instance
event_bus = EventBus()


# Standard event types (for documentation)
class EventTypes:
    """Standard event type constants"""

    # Blog events
    BLOG_POST_CREATED = "blog.post_created"
    BLOG_POST_PUBLISHED = "blog.post_published"
    BLOG_POST_UPDATED = "blog.post_updated"
    BLOG_POST_DELETED = "blog.post_deleted"

    # Comment events
    COMMENT_CREATED = "comment.created"
    COMMENT_APPROVED = "comment.approved"
    COMMENT_REJECTED = "comment.rejected"
    COMMENT_SPAM = "comment.marked_spam"

    # User events
    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_LOGOUT = "user.logout"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_EMAIL_VERIFIED = "user.email_verified"

    # Security events
    SECURITY_LOGIN_FAILED = "security.login_failed"
    SECURITY_SUSPICIOUS_ACTIVITY = "security.suspicious_activity"
    SECURITY_ACCOUNT_LOCKED = "security.account_locked"

    # Distribution events
    DISTRIBUTION_SCHEDULED = "distribution.scheduled"
    DISTRIBUTION_PUBLISHED = "distribution.published"
    DISTRIBUTION_FAILED = "distribution.failed"

    # Wallet events
    WALLET_CREDITED = "wallet.credited"
    WALLET_DEBITED = "wallet.debited"
    WALLET_ESCROW_LOCKED = "wallet.escrow_locked"
    WALLET_ESCROW_RELEASED = "wallet.escrow_released"


__all__ = [
    "HANDLER_ERRORS",
    "MAX_HANDLER_RETRIES",
    "RETRYABLE_ERRORS",
    "DeliveryFailure",
    "DomainError",
    "EventBus",
    "EventTypes",
    "event_bus",
    "handler_name",
]
