"""
Storage Service - File Storage Abstraction
===========================================

Unified interface for file storage operations.
Supports multiple backends: Local filesystem, S3, GCS, Azure.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class StorageBackend(ABC):
    """Abstract storage backend interface"""

    @abstractmethod
    def save(self, name: str, content) -> str:
        """Save a file and return its path"""
        pass

    @abstractmethod
    def url(self, name: str) -> str:
        """Get public URL for a file"""
        pass

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Delete a file"""
        pass

    @abstractmethod
    def exists(self, name: str) -> bool:
        """Check if file exists"""
        pass

    @abstractmethod
    def size(self, name: str) -> int:
        """Get file size in bytes"""
        pass


class LocalStorageBackend(StorageBackend):
    """Local filesystem storage"""

    def __init__(self):
        from django.core.files.storage import FileSystemStorage

        self.storage = FileSystemStorage()

    def save(self, name: str, content) -> str:
        return self.storage.save(name, content)

    def url(self, name: str) -> str:
        return self.storage.url(name)

    def delete(self, name: str) -> bool:
        self.storage.delete(name)
        return True

    def exists(self, name: str) -> bool:
        return self.storage.exists(name)

    def size(self, name: str) -> int:
        return self.storage.size(name)


class S3StorageBackend(StorageBackend):
    """AWS S3 storage"""

    def __init__(self):
        try:
            from storages.backends.s3boto3 import (  # type: ignore[import-not-found]
                S3Boto3Storage,
            )

            self.storage = S3Boto3Storage()
        except ImportError:
            logger.exception("django-storages[s3] not installed")
            raise ImportError("Install with: pip install django-storages[boto3]")  # noqa: B904

    def save(self, name: str, content) -> str:
        return self.storage.save(name, content)

    def url(self, name: str) -> str:
        return self.storage.url(name)

    def delete(self, name: str) -> bool:
        self.storage.delete(name)
        return True

    def exists(self, name: str) -> bool:
        return self.storage.exists(name)

    def size(self, name: str) -> int:
        return self.storage.size(name)


class GCSStorageBackend(StorageBackend):
    """Google Cloud Storage"""

    def __init__(self):
        try:
            from storages.backends.gcloud import (  # type: ignore[import-not-found]
                GoogleCloudStorage,
            )

            self.storage = GoogleCloudStorage()
        except ImportError:
            logger.exception("django-storages[google] not installed")
            raise ImportError("Install with: pip install django-storages[google]")  # noqa: B904

    def save(self, name: str, content) -> str:
        return self.storage.save(name, content)

    def url(self, name: str) -> str:
        return self.storage.url(name)

    def delete(self, name: str) -> bool:
        self.storage.delete(name)
        return True

    def exists(self, name: str) -> bool:
        return self.storage.exists(name)

    def size(self, name: str) -> int:
        return self.storage.size(name)


class AzureStorageBackend(StorageBackend):
    """Azure Blob Storage"""

    def __init__(self):
        try:
            from storages.backends.azure_storage import (  # type: ignore[import-not-found]
                AzureStorage,
            )

            self.storage = AzureStorage()
        except ImportError:
            logger.exception("django-storages[azure] not installed")
            raise ImportError("Install with: pip install django-storages[azure]")  # noqa: B904

    def save(self, name: str, content) -> str:
        return self.storage.save(name, content)

    def url(self, name: str) -> str:
        return self.storage.url(name)

    def delete(self, name: str) -> bool:
        self.storage.delete(name)
        return True

    def exists(self, name: str) -> bool:
        return self.storage.exists(name)

    def size(self, name: str) -> int:
        return self.storage.size(name)


class StorageService:
    """
    Unified storage service.

    Usage:
        storage = StorageService()
        path = storage.save_file('uploads/avatar.jpg', file_content)
        url = storage.get_url(path)
        storage.delete_file(path)
    """

    def __init__(self, backend: str | None = None):
        from django.conf import settings

        resolved: str = backend or getattr(settings, "STORAGE_BACKEND", "local")

        backends: dict[str, type[StorageBackend]] = {
            "local": LocalStorageBackend,
            "s3": S3StorageBackend,
            "gcs": GCSStorageBackend,
            "azure": AzureStorageBackend,
        }

        backend_class = backends.get(resolved)
        if not backend_class:
            logger.warning(f"Unknown storage backend '{backend}', using local")
            backend_class = LocalStorageBackend

        self.backend = backend_class()
        self.backend_name = backend
        logger.info(f"StorageService initialized with {backend} backend")

    def save_file(self, name: str, content) -> str:
        """
        Save a file and return its path/name.

        Args:
            name: Desired file path/name
            content: File content (File object or bytes)

        Returns:
            Actual path/name where file was saved
        """
        return self.backend.save(name, content)

    def get_url(self, name: str) -> str:
        """
        Get public URL for a file.

        Args:
            name: File path/name

        Returns:
            Public URL to access the file
        """
        return self.backend.url(name)

    def delete_file(self, name: str) -> bool:
        """
        Delete a file.

        Args:
            name: File path/name

        Returns:
            True if successful
        """
        try:
            return self.backend.delete(name)
        except Exception as e:
            logger.exception(f"Failed to delete file {name}: {e}")
            return False

    def file_exists(self, name: str) -> bool:
        """
        Check if file exists.

        Args:
            name: File path/name

        Returns:
            True if file exists
        """
        return self.backend.exists(name)

    def get_file_size(self, name: str) -> int:
        """
        Get file size in bytes.

        Args:
            name: File path/name

        Returns:
            File size in bytes
        """
        return self.backend.size(name)


__all__ = ["StorageService"]
