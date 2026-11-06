from __future__ import annotations

import os

from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible

from . import conf


@deconstructible
class PrivateStorage(FileSystemStorage):
    """Storage rooted at FILEMANAGER_ROOT, outside MEDIA_ROOT, with no public URL.

    The root is read from settings on every access so it follows overrides.
    """

    def __init__(self) -> None:
        super().__init__(base_url=None)

    @property
    def base_location(self) -> str:
        return str(conf.root())

    @property
    def location(self) -> str:
        return os.path.abspath(self.base_location)

    def url(self, name: str | None) -> str:
        raise ValueError("Private files have no public URL.")


def get_private_storage() -> PrivateStorage:
    return PrivateStorage()
