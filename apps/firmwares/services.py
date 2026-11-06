"""Firmware upload, external submission, moderation and routing services.

Moderation is app-owned: staff holding ``firmwares.change_pendingfirmware`` approve
or reject a ``PendingFirmware``. Approval routes it to the typed firmware table
(``is_verified=True`` makes it public) and records reviewer, timestamp and notes.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import stat
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from . import conf
from .constants import MAIN_CATEGORIES
from .crypto import decrypt_password, encrypt_password
from .models import (
    BaseFirmware,
    Brand,
    EngineeringFirmware,
    FirmwarePackageFormat,
    FirmwareSourceType,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    PendingFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
    Variant,
)
from .sources import validate_external_url

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_EXTENSIONS = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.xz",
    ".tar.md5",
    ".gz",
    ".xz",
    ".bin",
    ".img",
    ".mbn",
    ".pac",
    ".ofp",
    ".ops",
    ".nb0",
    ".lz4",
    ".scatter",
    ".md5",
)
ALLOWED_CONTENT_TYPES = {
    "",
    "text/plain",
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/gzip",
    "application/x-gzip",
    "application/x-xz",
    "application/x-compressed",
    "application/x-compressed-tar",
    "binary/octet-stream",
}
CATEGORY_KEYS = {key for key, _label in MAIN_CATEGORIES}
FIRMWARE_MODELS: dict[str, type[BaseFirmware]] = {
    "official": OfficialFirmware,
    "engineering": EngineeringFirmware,
    "readback": ReadbackFirmware,
    "modified": ModifiedFirmware,
    "other": OtherFirmware,
    "unclassified": UnclassifiedFirmware,
}
SUBTYPE_CATEGORIES = {"engineering", "modified", "other"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CHUNK_SIZE = 1024 * 1024
MAX_NOTES_LENGTH = 2000
REVIEW_PERMISSION = "firmwares.change_pendingfirmware"


class FirmwareUploadError(ValueError):
    """Raised when an upload or external submission is rejected."""


def storage_root() -> Path:
    configured = getattr(settings, "FIRMWARE_STORAGE_ROOT", None)
    return Path(configured) if configured is not None else conf.base_dir() / "storage"


def max_upload_bytes() -> int:
    return int(getattr(settings, "FIRMWARE_MAX_UPLOAD_BYTES", 100 * 1024 * 1024))


def allowed_extensions() -> tuple[str, ...]:
    configured = getattr(
        settings, "FIRMWARE_ALLOWED_EXTENSIONS", DEFAULT_ALLOWED_EXTENSIONS
    )
    return tuple(str(item).lower() for item in configured)


def calc_storage_path(brand_slug, model_slug, variant_slug, category, fw_id):
    return (
        storage_root() / brand_slug / model_slug / variant_slug / category / str(fw_id)
    )


def _safe_filename(name: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = re.sub(r"[\x00-\x1f]", "", base).strip()[:255]
    if not base or base.startswith("."):
        raise FirmwareUploadError("A valid file name is required.")
    return base


def _validate_file_name(name: str) -> None:
    lowered = name.lower()
    if not lowered.endswith(allowed_extensions()):
        raise FirmwareUploadError("This file type is not accepted for firmware upload.")


def _is_within(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def handle_upload(
    *,
    uploader,
    uploaded_brand,
    uploaded_model,
    uploaded_variant,
    file_obj,
    is_password_protected,
    password,
    extra_info=None,
    package_format: str = FirmwarePackageFormat.UNKNOWN,
):
    """Stage an uploaded file, hash it, and create its pending record."""
    if package_format not in FirmwarePackageFormat.values:
        raise ValueError("Unsupported firmware package format.")

    safe_name = _safe_filename(file_obj.name)
    _validate_file_name(safe_name)
    content_type = (getattr(file_obj, "content_type", "") or "").split(";")[0].lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise FirmwareUploadError("This content type is not accepted for firmware.")
    limit = max_upload_bytes()
    declared = getattr(file_obj, "size", None)
    if declared is not None and declared > limit:
        raise FirmwareUploadError(
            f"Upload exceeds maximum allowed size of {limit} bytes"
        )

    tmp_dir = storage_root() / "pending" / str(uuid.uuid4())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    file_path = tmp_dir / safe_name
    digest = hashlib.sha256()
    written = 0
    try:
        with open(file_path, "wb") as handle:
            for chunk in file_obj.chunks(CHUNK_SIZE):
                written += len(chunk)
                if written > limit:
                    raise FirmwareUploadError(
                        f"Upload exceeds maximum allowed size of {limit} bytes"
                    )
                digest.update(chunk)
                handle.write(chunk)
        if written == 0:
            raise FirmwareUploadError("The uploaded file is empty.")
        password_token = (
            encrypt_password(password) if (is_password_protected and password) else ""
        )
        return PendingFirmware.objects.create(
            original_file_name=safe_name,
            source_type=FirmwareSourceType.STORED,
            stored_file_path=str(file_path),
            file_size=written,
            file_hash=digest.hexdigest(),
            uploader=uploader,
            uploaded_brand=uploaded_brand,
            uploaded_model=uploaded_model,
            uploaded_variant=uploaded_variant,
            package_format=package_format,
            is_password_protected=is_password_protected,
            encrypted_password=password_token,
            metadata={"extra_info": extra_info} if extra_info else {},
        )
    except (FirmwareUploadError, DatabaseError, OSError):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def submit_external_firmware(
    *,
    uploader,
    url: str,
    original_file_name: str,
    uploaded_brand=None,
    uploaded_model=None,
    uploaded_variant=None,
    package_format: str = FirmwarePackageFormat.UNKNOWN,
    extra_info: str | None = None,
    file_size: int | None = None,
    file_hash: str = "",
):
    """Create a pending record for a file hosted on an allowlisted external host.

    Idempotent: resubmitting a URL that is still pending returns the same record.
    """
    if package_format not in FirmwarePackageFormat.values:
        raise ValueError("Unsupported firmware package format.")
    safe_name = _safe_filename(original_file_name)
    _validate_file_name(safe_name)
    clean_url = validate_external_url(url)
    clean_hash = (file_hash or "").strip().lower()
    if clean_hash and not SHA256_PATTERN.match(clean_hash):
        raise FirmwareUploadError("file_hash must be a SHA-256 hex digest.")
    if file_size is not None and file_size < 0:
        raise FirmwareUploadError("file_size must not be negative.")
    with transaction.atomic():
        existing = (
            PendingFirmware.objects.select_for_update()
            .filter(
                source_type=FirmwareSourceType.EXTERNAL,
                external_url=clean_url,
                admin_decision="pending",
            )
            .first()
        )
        if existing is not None:
            return existing
        return PendingFirmware.objects.create(
            original_file_name=safe_name,
            source_type=FirmwareSourceType.EXTERNAL,
            external_url=clean_url,
            file_size=file_size,
            file_hash=clean_hash,
            uploader=uploader,
            uploaded_brand=uploaded_brand,
            uploaded_model=uploaded_model,
            uploaded_variant=uploaded_variant,
            package_format=package_format,
            metadata={"extra_info": extra_info} if extra_info else {},
        )


def _archive_limits() -> tuple[int, int]:
    return (
        int(getattr(settings, "FIRMWARE_ZIP_MAX_MEMBERS", 200)),
        int(getattr(settings, "FIRMWARE_ZIP_MAX_TOTAL_BYTES", 500 * 1024 * 1024)),
    )


def _inspect_archive(archive: zipfile.ZipFile, dest_dir: Path) -> list[zipfile.ZipInfo]:
    """Reject traversal, symlink and oversized archives before any data is read."""
    max_members, max_total = _archive_limits()
    members: list[zipfile.ZipInfo] = []
    total = 0
    for member in archive.infolist():
        if member.is_dir():
            continue
        if len(members) + 1 > max_members:
            raise ValueError("Archive has too many entries")
        total += member.file_size
        if total > max_total or member.file_size > max_total:
            raise ValueError("Archive exceeds allowed uncompressed size")
        if (
            not (dest_dir / member.filename)
            .resolve()
            .is_relative_to(dest_dir.resolve())
        ):
            raise ValueError("Archive entry escapes the extraction directory")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise ValueError("Symlink detected in archive")
        members.append(member)
    return members


def attempt_extraction(pf: PendingFirmware) -> None:
    """Validate a password-protected archive in place without extracting it."""
    if pf.source_type != FirmwareSourceType.STORED or not pf.is_password_protected:
        pf.extraction_status = (
            "success" if pf.source_type == FirmwareSourceType.STORED else "pending"
        )
        pf.save(update_fields=["extraction_status"])
        return
    try:
        password = (
            decrypt_password(pf.encrypted_password) if pf.encrypted_password else ""
        )
    except (ValueError, TypeError, RuntimeError) as exc:
        logger.warning("Stored archive password for %s is unreadable: %s", pf.pk, exc)
        pf.password_validation_status = "invalid"
        pf.extraction_status = "failed"
        pf.save(update_fields=["password_validation_status", "extraction_status"])
        return
    path = Path(pf.stored_file_path)
    try:
        if not zipfile.is_zipfile(path):
            pf.password_validation_status = "unknown"
            pf.extraction_status = "pending"
        else:
            with zipfile.ZipFile(path) as archive:
                members = _inspect_archive(archive, path.parent)
                pwd = password.encode() if password else None
                for member in members:
                    with archive.open(member, pwd=pwd) as handle:
                        while handle.read(CHUNK_SIZE):
                            pass
            pf.password_validation_status = "valid"
            pf.extraction_status = "success"
    except (zipfile.BadZipFile, RuntimeError, ValueError, OSError, NotImplementedError):
        logger.warning("Archive validation failed for %s", pf.pk, exc_info=True)
        pf.password_validation_status = "invalid"
        pf.extraction_status = "failed"
    pf.save(update_fields=["password_validation_status", "extraction_status"])


def run_ai_analysis(pf: PendingFirmware) -> None:
    """Record content-derived metadata suggestions; never decides the category."""
    from .ai_analysis import FirmwareAIAnalyzer

    if pf.source_type != FirmwareSourceType.STORED:
        return
    result = FirmwareAIAnalyzer.analyze_firmware(pf.stored_file_path)
    if result.get("error"):
        pf.metadata = {**pf.metadata, "ai_error": "analysis_failed"}
        pf.save(update_fields=["metadata"])
        return
    FirmwareAIAnalyzer.apply_to_pending(pf, result)
    pf.save()


def _require_reviewer(user) -> None:
    if not (
        getattr(user, "is_authenticated", False)
        and user.is_active
        and user.is_staff
        and user.has_perm(REVIEW_PERMISSION)
    ):
        raise PermissionDenied("Firmware review permission is required.")


def _clean_notes(notes: str, *, required: bool = False) -> str:
    value = (notes or "").strip()
    if required and not value:
        raise ValidationError("A reason is required for this decision.")
    if len(value) > MAX_NOTES_LENGTH:
        raise ValidationError(f"Notes must not exceed {MAX_NOTES_LENGTH} characters.")
    return value


def _validate_hierarchy(brand, model, variant):
    if variant is not None and model is None:
        model = variant.model
    if model is not None and brand is None:
        brand = model.brand
    if model is not None and brand is not None and model.brand_id != brand.pk:
        raise ValidationError("The selected model does not belong to the brand.")
    if variant is not None and model is not None and variant.model_id != model.pk:
        raise ValidationError("The selected variant does not belong to the model.")
    return brand, model, variant


def _notify(pf: PendingFirmware, *, title: str, message: str, priority: str) -> None:
    if pf.uploader_id is None:
        return
    from apps.core.signals import notification_send_requested

    for receiver, response in notification_send_requested.send_robust(
        sender=PendingFirmware,
        user_ids=[pf.uploader_id],
        action_type="moderation",
        priority=priority,
        title=title,
        message=message,
    ):
        if isinstance(response, Exception):
            logger.error(
                "Firmware notification receiver %r failed for %s: %s",
                receiver,
                pf.pk,
                response,
            )


def routed_record(pf: PendingFirmware):
    """Return the typed firmware record an approved pending upload became."""
    model_class = FIRMWARE_MODELS.get(pf.routed_category)
    if model_class is None or pf.routed_object_id is None:
        return None
    return model_class.objects.filter(pk=pf.routed_object_id).first()


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def approve_pending_firmware(
    pf: PendingFirmware,
    admin_user,
    *,
    category: str | None = None,
    subtype: str | None = None,
    brand: Brand | None = None,
    model: Model | None = None,
    variant: Variant | None = None,
    notes: str = "",
    unclassified_reason: str = "",
    package_format: str | None = None,
    chipset: str | None = None,
    build_number: str | None = None,
    android_version: str | None = None,
    security_patch: date | None = None,
    build_date: date | None = None,
):
    """Approve a pending upload, route it to its typed table and make it public.

    Idempotent: approving an already approved upload returns its routed record.
    The reviewer must choose the category; an unspecified one is ``unclassified``.
    Build number, Android version, security patch and build date are optional
    reviewer-confirmed release details recorded on the published record.
    """
    _require_reviewer(admin_user)
    clean_notes = _clean_notes(notes)
    final_category = category or "unclassified"
    if final_category not in CATEGORY_KEYS:
        raise ValidationError("Unsupported firmware category.")
    if package_format and package_format not in FirmwarePackageFormat.values:
        raise ValidationError("Unsupported firmware package format.")

    with transaction.atomic():
        locked = PendingFirmware.objects.select_for_update().get(pk=pf.pk)
        if locked.admin_decision == "approved":
            existing = routed_record(locked)
            if existing is not None:
                return existing
            raise ValidationError("This upload was approved but has no routed record.")
        if locked.admin_decision != "pending":
            raise ValidationError("This upload is no longer pending review.")

        target_brand, target_model, target_variant = _validate_hierarchy(
            brand or locked.uploaded_brand,
            model or locked.uploaded_model,
            variant or locked.uploaded_variant,
        )
        now = timezone.now()
        base_kwargs: dict[str, Any] = {
            "original_file_name": locked.original_file_name,
            "source_type": locked.source_type,
            "stored_file_path": "",
            "external_url": locked.external_url,
            "uploader": locked.uploader,
            "brand": target_brand,
            "model": target_model,
            "variant": target_variant,
            "package_format": package_format or locked.package_format,
            "chipset": chipset if chipset is not None else locked.chipset,
            "partitions": locked.partitions,
            "is_password_protected": locked.is_password_protected,
            "encrypted_password": locked.encrypted_password,
            "metadata": locked.metadata,
            "file_size": locked.file_size,
            "file_hash": locked.file_hash,
            "is_verified": True,
            "reviewed_by": admin_user,
            "reviewed_at": now,
        }
        release_details = {
            "build_number": build_number,
            "android_version": android_version,
            "security_patch": security_patch,
            "build_date": build_date,
        }
        base_kwargs.update(
            {key: value for key, value in release_details.items() if value is not None}
        )

        source_path = Path(locked.stored_file_path) if locked.stored_file_path else None
        final_path: Path | None = None
        moved_here = False
        if locked.source_type == FirmwareSourceType.STORED:
            target_dir = calc_storage_path(
                target_brand.slug if target_brand else "unknown",
                target_model.slug if target_model else "unknown",
                target_variant.slug if target_variant else "unknown",
                final_category,
                locked.pk,
            )
            target_dir.mkdir(parents=True, exist_ok=True)
            final_path = target_dir / _safe_filename(locked.original_file_name)
            if source_path is not None and source_path.is_file():
                if final_path.exists():
                    raise ValidationError("The firmware destination already exists.")
                shutil.move(str(source_path), str(final_path))
                moved_here = True
            elif not final_path.is_file():
                raise ValidationError("The staged firmware file is missing.")
            base_kwargs["stored_file_path"] = str(final_path)
            if not base_kwargs["file_hash"] or base_kwargs["file_size"] is None:
                size, digest = _hash_file(final_path)
                base_kwargs["file_size"], base_kwargs["file_hash"] = size, digest

        model_class = FIRMWARE_MODELS[final_category]
        extra: dict[str, Any] = {}
        if final_category in SUBTYPE_CATEGORIES:
            extra["subtype"] = (subtype or "")[:128]
        if final_category == "unclassified":
            extra["reason"] = (unclassified_reason or "unclassified")[:128]
        try:
            with transaction.atomic():
                record = model_class.objects.create(**base_kwargs, **extra)
            locked.admin_decision = "approved"
            locked.admin_notes = clean_notes
            locked.category_locked = True
            locked.reviewed_by = admin_user
            locked.reviewed_at = now
            locked.routed_category = final_category
            locked.routed_object_id = record.pk
            locked.chipset = base_kwargs["chipset"]
            locked.file_size = base_kwargs["file_size"]
            locked.file_hash = base_kwargs["file_hash"]
            locked.save()
        except (DatabaseError, ValidationError) as exc:
            if moved_here and final_path is not None and source_path is not None:
                shutil.move(str(final_path), str(source_path))
            if isinstance(exc, IntegrityError):
                raise ValidationError(
                    "A firmware record for this file and build already exists."
                ) from exc
            raise
        transaction.on_commit(
            lambda: _notify(
                locked,
                title="Firmware Approved",
                message=f'Your firmware upload "{locked.original_file_name}" has been approved.',
                priority="info",
            )
        )
    logger.info(
        "Firmware %s approved as %s by user %s",
        locked.pk,
        final_category,
        admin_user.pk,
    )
    return record


def reject_pending_firmware(pf: PendingFirmware, admin_user, *, notes: str):
    """Reject a pending upload, delete its staged file and notify the uploader."""
    _require_reviewer(admin_user)
    clean_notes = _clean_notes(notes, required=True)
    with transaction.atomic():
        locked = PendingFirmware.objects.select_for_update().get(pk=pf.pk)
        if locked.admin_decision == "rejected":
            return locked
        if locked.admin_decision != "pending":
            raise ValidationError("This upload is no longer pending review.")
        staged = locked.stored_file_path
        locked.admin_decision = "rejected"
        locked.admin_notes = clean_notes
        locked.reviewed_by = admin_user
        locked.reviewed_at = timezone.now()
        locked.save()
        transaction.on_commit(lambda: _discard_staged_file(staged))
        transaction.on_commit(
            lambda: _notify(
                locked,
                title="Firmware Rejected",
                message=(
                    f'Your firmware upload "{locked.original_file_name}" was '
                    f"rejected. Reason: {clean_notes}"
                ),
                priority="warning",
            )
        )
    logger.info("Firmware %s rejected by user %s", locked.pk, admin_user.pk)
    return locked


def _discard_staged_file(path_text: str) -> None:
    if not path_text:
        return
    path = Path(path_text)
    try:
        if _is_within(path, storage_root() / "pending") and path.is_file():
            path.unlink()
            parent = path.parent
            if not any(parent.iterdir()):
                parent.rmdir()
    except OSError:
        logger.exception("Could not delete rejected firmware file %s", path)


def withdraw_firmware(pf: PendingFirmware, admin_user, *, reason: str):
    """Pull an approved upload back to pending so it can be corrected and re-approved."""
    _require_reviewer(admin_user)
    clean_reason = _clean_notes(reason, required=True)
    with transaction.atomic():
        locked = PendingFirmware.objects.select_for_update().get(pk=pf.pk)
        if locked.admin_decision != "approved":
            raise ValidationError("Only approved uploads can be withdrawn.")
        record = routed_record(locked)
        if record is not None:
            if (
                record.source_type == FirmwareSourceType.STORED
                and record.stored_file_path
            ):
                staged = storage_root() / "pending" / str(uuid.uuid4())
                staged.mkdir(parents=True, exist_ok=True)
                destination = staged / Path(record.stored_file_path).name
                shutil.move(record.stored_file_path, str(destination))
                locked.stored_file_path = str(destination)
            record.delete()
        locked.admin_decision = "pending"
        locked.admin_notes = clean_reason
        locked.category_locked = False
        locked.reviewed_by = admin_user
        locked.reviewed_at = timezone.now()
        locked.routed_category = ""
        locked.routed_object_id = None
        locked.save()
    logger.info("Firmware %s withdrawn by user %s", locked.pk, admin_user.pk)
    return locked


def moderate_and_route(
    pf: PendingFirmware,
    decision: str,
    *,
    admin_user,
    category: str | None = None,
    subtype: str | None = None,
    brand: Brand | None = None,
    model: Model | None = None,
    variant: Variant | None = None,
    notes: str = "",
    unclassified_reason: str = "",
    package_format: str | None = None,
):
    """Apply an ``approved`` or ``rejected`` decision to a pending upload."""
    if decision == "approved":
        return approve_pending_firmware(
            pf,
            admin_user,
            category=category,
            subtype=subtype,
            brand=brand,
            model=model,
            variant=variant,
            notes=notes,
            unclassified_reason=unclassified_reason,
            package_format=package_format,
        )
    if decision == "rejected":
        return reject_pending_firmware(pf, admin_user, notes=notes)
    raise ValidationError("Decision must be approved or rejected.")
