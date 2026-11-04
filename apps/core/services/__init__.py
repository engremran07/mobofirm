"""
Core Services Package
Provides service implementations for cross-app communication.
"""

from .notifications import notification_service
from .settings import settings_provider

__all__ = ["notification_service", "settings_provider"]
