"""
Event Bus - Internal Event System
==================================

Decoupled event system for app-to-app communication.
"""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class EventBus:
    """
    Internal event bus for decoupled app communication.

    Usage:
        # Publishing events
        event_bus.publish('blog.post_published', {'post_id': 123})

        # Subscribing to events
        @event_bus.subscribe('blog.post_published')
        def on_post_published(data):
            logger.info(f"Post {data['post_id']} was published!")
    """

    _instance = None
    _handlers: dict[str, list[Callable]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def subscribe(self, event_type: str, handler: Callable | None = None):
        """
        Subscribe to an event.

        Can be used as decorator:
            @event_bus.subscribe('event.name')
            def handler(data):
                pass

        Or as method:
            event_bus.subscribe('event.name', handler_function)

        Args:
            event_type: Event identifier (e.g., 'blog.post_published')
            handler: Handler function (if not used as decorator)
        """

        def decorator(func: Callable):
            if event_type not in self._handlers:
                self._handlers[event_type] = []

            self._handlers[event_type].append(func)
            logger.debug(f"Subscribed {func.__name__} to {event_type}")
            return func

        if handler is not None:
            return decorator(handler)
        return decorator

    def publish(self, event_type: str, data: Any = None):
        """
        Publish an event to all subscribers.

        Args:
            event_type: Event identifier
            data: Event data (dictionary, object, etc.)
        """
        handlers = self._handlers.get(event_type, [])

        if not handlers:
            logger.debug(f"No handlers for event '{event_type}'")
            return

        logger.info(f"Publishing event '{event_type}' to {len(handlers)} handlers")

        for handler in handlers:
            try:
                handler(data)
            except Exception as e:
                logger.error(
                    f"Event handler {handler.__name__} failed for {event_type}: {e}",
                    exc_info=True,
                )

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
                logger.debug(f"Unsubscribed {handler.__name__} from {event_type}")
            except ValueError:
                pass

    def clear_handlers(self, event_type: str | None = None):
        """
        Clear all handlers for an event type, or all handlers.

        Args:
            event_type: Event identifier (clears all if None)
        """
        if event_type:
            self._handlers.pop(event_type, None)
            logger.debug(f"Cleared handlers for {event_type}")
        else:
            self._handlers.clear()
            logger.debug("Cleared all event handlers")

    def get_event_types(self) -> list[str]:
        """Get all registered event types"""
        return list(self._handlers.keys())

    def get_handler_count(self, event_type: str) -> int:
        """Get number of handlers for an event type"""
        return len(self._handlers.get(event_type, []))


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


__all__ = ["EventBus", "EventTypes", "event_bus"]
