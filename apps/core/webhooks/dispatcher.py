"""
Webhook Dispatcher - External Event Notifications
==================================================

Send webhooks to external systems when events occur.
"""

import logging
from typing import Any

import requests
from django.db import models

logger = logging.getLogger(__name__)


class Webhook(models.Model):
    """Webhook configuration"""

    name = models.CharField(max_length=200)
    url = models.URLField()
    event_type = models.CharField(max_length=100, db_index=True)
    secret = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)

    # Headers to include in request
    headers = models.JSONField(default=dict, blank=True)

    # Delivery settings
    retry_count = models.PositiveIntegerField(default=3)
    timeout_seconds = models.PositiveIntegerField(default=30)

    # Statistics
    total_deliveries = models.PositiveIntegerField(default=0)
    successful_deliveries = models.PositiveIntegerField(default=0)
    failed_deliveries = models.PositiveIntegerField(default=0)
    last_delivery_at = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "active"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.event_type})"


class WebhookDelivery(models.Model):  # noqa: DJ008
    """Log of webhook deliveries"""

    webhook = models.ForeignKey(
        Webhook, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()

    # Response
    status_code = models.PositiveIntegerField(null=True, blank=True)
    response_body = models.TextField(blank=True)
    error_message = models.TextField(blank=True)

    # Timing
    attempt_number = models.PositiveIntegerField(default=1)
    delivered_at = models.DateTimeField(auto_now_add=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    # Success
    success = models.BooleanField(default=False)

    class Meta:
        ordering = ["-delivered_at"]
        indexes = [
            models.Index(fields=["webhook", "success"]),
        ]


class WebhookDispatcher:
    """
    Dispatch webhooks to external systems.

    Usage:
        dispatcher = WebhookDispatcher()
        dispatcher.dispatch('blog.post_published', {'post_id': 123, 'title': 'Hello'})
    """

    def dispatch(self, event_type: str, payload: dict[str, Any]):
        """
        Dispatch event to all registered webhooks.

        Args:
            event_type: Event identifier (e.g., 'blog.post_published')
            payload: Event data to send
        """
        webhooks = Webhook.objects.filter(event_type=event_type, active=True)

        if not webhooks.exists():
            logger.debug(f"No webhooks registered for '{event_type}'")
            return

        logger.info(f"Dispatching '{event_type}' to {webhooks.count()} webhooks")

        for webhook in webhooks:
            self._send_webhook(webhook, event_type, payload)

    def _send_webhook(self, webhook: Webhook, event_type: str, payload: dict[str, Any]):
        """Send webhook with retries"""
        import time

        from django.utils import timezone

        for attempt in range(1, webhook.retry_count + 1):
            start_time = time.time()

            try:
                # Prepare request
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "platform-Webhook/1.0",
                    "X-Event-Type": event_type,
                    **webhook.headers,
                }

                # Add signature if secret provided
                if webhook.secret:
                    import hashlib
                    import hmac
                    import json

                    message = json.dumps(payload, sort_keys=True)
                    signature = hmac.new(
                        webhook.secret.encode(), message.encode(), hashlib.sha256
                    ).hexdigest()
                    headers["X-Webhook-Signature"] = f"sha256={signature}"

                # Send request
                response = requests.post(
                    webhook.url,
                    json=payload,
                    headers=headers,
                    timeout=webhook.timeout_seconds,
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Log delivery
                success = 200 <= response.status_code < 300

                WebhookDelivery.objects.create(
                    webhook=webhook,
                    event_type=event_type,
                    payload=payload,
                    status_code=response.status_code,
                    response_body=response.text[:1000],  # Truncate
                    success=success,
                    attempt_number=attempt,
                    duration_ms=duration_ms,
                )

                # Update webhook stats
                webhook.total_deliveries += 1
                webhook.last_delivery_at = timezone.now()
                webhook.last_status_code = response.status_code

                if success:
                    webhook.successful_deliveries += 1
                    webhook.save()
                    logger.info(
                        f"Webhook delivered to {webhook.url} ({response.status_code})"
                    )
                    return  # Success, don't retry
                else:
                    webhook.failed_deliveries += 1
                    webhook.save()
                    logger.warning(
                        f"Webhook failed: {response.status_code} - {response.text[:200]}"
                    )

            except requests.Timeout:
                logger.exception(f"Webhook timeout to {webhook.url}")
                self._log_failed_delivery(
                    webhook, event_type, payload, attempt, "Timeout"
                )

            except requests.RequestException as e:
                logger.exception(f"Webhook request failed to {webhook.url}: {e}")
                self._log_failed_delivery(webhook, event_type, payload, attempt, str(e))

            except Exception as e:
                logger.error(f"Webhook dispatch error: {e}", exc_info=True)
                self._log_failed_delivery(webhook, event_type, payload, attempt, str(e))

            # Wait before retry (exponential backoff)
            if attempt < webhook.retry_count:
                wait_seconds = 2**attempt
                logger.info(
                    f"Retrying webhook in {wait_seconds}s (attempt {attempt + 1}/{webhook.retry_count})"
                )
                time.sleep(wait_seconds)

    def _log_failed_delivery(
        self,
        webhook: Webhook,
        event_type: str,
        payload: dict[str, Any],
        attempt: int,
        error: str,
    ):
        """Log a failed delivery"""
        from django.utils import timezone

        WebhookDelivery.objects.create(
            webhook=webhook,
            event_type=event_type,
            payload=payload,
            error_message=error,
            success=False,
            attempt_number=attempt,
        )

        webhook.total_deliveries += 1
        webhook.failed_deliveries += 1
        webhook.last_delivery_at = timezone.now()
        webhook.save()


__all__ = ["Webhook", "WebhookDelivery", "WebhookDispatcher"]
