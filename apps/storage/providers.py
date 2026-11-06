"""Storage provider catalogue and local-path/URL safety rules."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from apps.firmwares import sources


class StoragePathError(ValueError):
    """Raised when a stored path would leave the configured storage root."""


class StorageUrlError(ValueError):
    """Raised when an external download URL is not acceptable."""


LOCAL = "local"
EXTERNAL_LINK = "external_link"
GOOGLE_DRIVE = "google_drive"
S3 = "s3"

PROVIDER_CHOICES = [
    (LOCAL, "Local filesystem"),
    (EXTERNAL_LINK, "External link (allow-listed host)"),
    (GOOGLE_DRIVE, "Google Drive"),
    (S3, "Amazon S3"),
]


@dataclass(frozen=True)
class ProviderStatus:
    kind: str
    label: str
    usable: bool
    detail: str


def _setting(name: str) -> object:
    return getattr(settings, name)


def storage_root() -> Path:
    return Path(str(_setting("FIRMWARE_STORAGE_ROOT"))).resolve()


def resolve_local_path(relative_path: str) -> Path:
    """Resolve a path stored relative to the storage root, refusing any escape."""
    if not relative_path:
        raise StoragePathError("Empty storage path.")
    root = storage_root()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise StoragePathError("Path escapes the storage root.")
    return candidate


def relative_to_root(stored_path: str) -> str:
    """Return a storage-root-relative POSIX path for an absolute or relative path."""
    candidate = Path(stored_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (storage_root() / candidate).resolve()
    )
    root = storage_root()
    if not resolved.is_relative_to(root):
        raise StoragePathError("Path is outside the storage root.")
    return resolved.relative_to(root).as_posix()


def external_host_allowed(url: str) -> bool:
    try:
        sources.validate_external_url(url)
    except ValidationError:
        return False
    return True


def validate_external_url(url: str) -> str:
    """Accept only URLs the firmware catalogue itself would accept."""
    try:
        return sources.validate_external_url(url)
    except ValidationError as exc:
        raise StorageUrlError(" ".join(exc.messages)) from exc


def _package_installed(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _cloud_status(
    kind: str, label: str, flag: bool, packages: tuple[str, ...], flag_name: str
) -> ProviderStatus:
    missing = [package for package in packages if not _package_installed(package)]
    if not flag:
        return ProviderStatus(kind, label, False, f"Disabled: {flag_name} is false.")
    if missing:
        return ProviderStatus(
            kind,
            label,
            False,
            f"Disabled: required packages are not installed ({', '.join(missing)}).",
        )
    return ProviderStatus(
        kind, label, False, "Disabled: no approved credential store or adapter yet."
    )


def provider_statuses() -> list[ProviderStatus]:
    """Visible availability of every provider; cloud providers are never usable yet."""
    return [
        ProviderStatus(LOCAL, "Local filesystem", True, f"Root: {storage_root()}"),
        ProviderStatus(
            EXTERNAL_LINK,
            "External link",
            bool(sources.allowed_external_hosts()),
            "Allow-listed hosts (FIRMWARE_EXTERNAL_URL_ALLOWED_HOSTS): "
            + (", ".join(sources.allowed_external_hosts()) or "none"),
        ),
        _cloud_status(
            GOOGLE_DRIVE,
            "Google Drive",
            bool(_setting("STORAGE_GOOGLE_DRIVE_ENABLED")),
            ("googleapiclient", "google.oauth2"),
            "STORAGE_GOOGLE_DRIVE_ENABLED",
        ),
        _cloud_status(
            S3,
            "Amazon S3",
            bool(_setting("STORAGE_S3_ENABLED")),
            ("boto3",),
            "STORAGE_S3_ENABLED",
        ),
    ]


def provider_is_usable(kind: str) -> bool:
    return any(status.usable for status in provider_statuses() if status.kind == kind)
