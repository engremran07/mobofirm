"""
Core Service Interfaces
Abstract interfaces for decoupling apps from each other.
"""

from abc import ABC, abstractmethod
from typing import Any


class SettingsProvider(ABC):
    """Abstract interface for accessing site settings without direct model dependency"""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Get a setting value by key"""
        pass

    @abstractmethod
    def get_all(self) -> dict[str, Any]:
        """Get all settings as dictionary"""
        pass


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
        pass

    @abstractmethod
    def send_bulk(
        self,
        user_ids: list[int],
        title: str,
        message: str,
        notification_type: str = "info",
    ) -> int:
        """Send notification to multiple users, returns count sent"""
        pass


class CacheService(ABC):
    """Abstract interface for caching without direct dependency on cache backend"""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        """Get cached value"""
        pass

    @abstractmethod
    def set(self, key: str, value: Any, timeout: int | None = None) -> bool:
        """Set cached value"""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete cached value"""
        pass


class RateLimitService(ABC):
    """Abstract interface for rate limiting"""

    @abstractmethod
    def check_limit(self, identifier: str, limit: int, period: int) -> bool:
        """Check if rate limit is exceeded"""
        pass

    @abstractmethod
    def increment(self, identifier: str, period: int) -> int:
        """Increment counter and return current count"""
        pass
