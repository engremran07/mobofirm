"""
Webhooks Package
================

External event notification system.
"""

from .dispatcher import Webhook, WebhookDelivery, WebhookDispatcher

__all__ = ["Webhook", "WebhookDelivery", "WebhookDispatcher"]
