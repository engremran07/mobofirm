"""
Email Service - Email Abstraction
==================================

Unified interface for sending emails with template support.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class EmailBackend(ABC):
    """Abstract email backend interface"""

    @abstractmethod
    def send(
        self,
        subject: str,
        message: str,
        from_email: str,
        recipient_list: list[str],
        html_message: str | None = None,
    ) -> int:
        """Send an email"""
        pass


class DjangoEmailBackend(EmailBackend):
    """Django's built-in email backend"""

    def send(
        self,
        subject: str,
        message: str,
        from_email: str,
        recipient_list: list[str],
        html_message: str | None = None,
    ) -> int:
        from django.core.mail import send_mail

        try:
            return send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=recipient_list,
                html_message=html_message,
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"Email sending failed: {e}", exc_info=True)
            return 0


class EmailService:
    """
    Unified email service with template support.

    Usage:
        email = EmailService()
        email.send_email(
            subject="Welcome",
            message="Welcome to our site!",
            to_emails=["user@example.com"]
        )

        # Or with templates:
        email.send_templated_email(
            template_prefix='emails/welcome',
            to_emails=['user@example.com'],
            context={'username': 'John'}
        )
    """

    def __init__(self, backend: EmailBackend | None = None):
        self.backend = backend or DjangoEmailBackend()

    def send_email(
        self,
        subject: str,
        message: str,
        to_emails: list[str],
        from_email: str | None = None,
        html_message: str | None = None,
    ) -> int:
        """
        Send a simple email.

        Args:
            subject: Email subject
            message: Plain text message
            to_emails: List of recipient email addresses
            from_email: Sender email (uses DEFAULT_FROM_EMAIL if not provided)
            html_message: Optional HTML version of the message

        Returns:
            Number of emails sent
        """
        from django.conf import settings

        from_email = from_email or settings.DEFAULT_FROM_EMAIL

        return self.backend.send(
            subject=subject,
            message=message,
            from_email=from_email or "",
            recipient_list=to_emails,
            html_message=html_message,
        )

    def send_templated_email(
        self,
        template_prefix: str,
        to_emails: list[str],
        context: dict[str, Any],
        from_email: str | None = None,
    ) -> int:
        """
        Send email using templates.

        Looks for:
        - {template_prefix}_subject.txt (or .html)
        - {template_prefix}.txt (plain text body)
        - {template_prefix}.html (HTML body)

        Args:
            template_prefix: Path to template without extension (e.g., 'emails/welcome')
            to_emails: List of recipient email addresses
            context: Template context dictionary
            from_email: Sender email

        Returns:
            Number of emails sent
        """
        from django.template import TemplateDoesNotExist
        from django.template.loader import render_to_string

        try:
            # Render subject (remove newlines)
            try:
                subject = render_to_string(
                    f"{template_prefix}_subject.txt", context
                ).strip()
            except TemplateDoesNotExist:
                try:
                    subject = render_to_string(
                        f"{template_prefix}_subject.html", context
                    ).strip()
                except TemplateDoesNotExist:
                    subject = "New message"

            # Render plain text message
            try:
                text_message = render_to_string(f"{template_prefix}.txt", context)
            except TemplateDoesNotExist:
                text_message = ""

            # Render HTML message
            html_message = None
            try:
                html_message = render_to_string(f"{template_prefix}.html", context)
            except TemplateDoesNotExist:
                pass

            if not text_message and not html_message:
                logger.error(f"No email templates found for '{template_prefix}'")
                return 0

            return self.send_email(
                subject=subject,
                message=text_message,
                to_emails=to_emails,
                from_email=from_email,
                html_message=html_message,
            )

        except Exception as e:
            logger.error(f"Templated email failed: {e}", exc_info=True)
            return 0

    def send_bulk_email(
        self,
        subject: str,
        message: str,
        to_emails: list[str],
        from_email: str | None = None,
        batch_size: int = 100,
    ) -> int:
        """
        Send email to multiple recipients in batches.

        Args:
            subject: Email subject
            message: Email message
            to_emails: List of recipient emails
            from_email: Sender email
            batch_size: Number of emails per batch

        Returns:
            Total number of emails sent
        """
        total_sent = 0
        for i in range(0, len(to_emails), batch_size):
            batch = to_emails[i : i + batch_size]
            sent = self.send_email(
                subject=subject, message=message, to_emails=batch, from_email=from_email
            )
            total_sent += sent

        return total_sent


__all__ = ["EmailService"]
