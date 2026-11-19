"""
Core Service Interfaces
Abstract interfaces for decoupling apps from each other.
"""

from abc import ABC, abstractmethod


class NotificationService(ABC):
    """Abstract interface for sending notifications without direct model dependency"""

    @abstractmethod
    def send(
        self,
        user_id: int,
        title: str,
        message: str,
        notification_type: str = "info",
        **kwargs,
    ) -> bool:
        """Send a notification to a user"""

    @abstractmethod
    def send_bulk(
        self,
        user_ids: list[int],
        title: str,
        message: str,
        notification_type: str = "info",
    ) -> int:
        """Send notification to multiple users, returns count sent"""
