import json
import logging
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .crypto import decrypt_password, encrypt_password
from .models import (
    Brand,
    EngineeringFirmware,
    FirmwarePackageFormat,
    Model,
    ModifiedFirmware,
    OfficialFirmware,
    OtherFirmware,
    PendingFirmware,
    ReadbackFirmware,
    UnclassifiedFirmware,
    Variant,
)

logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = getattr(
    settings, "FIRMWARE_MAX_UPLOAD_BYTES", 100 * 1024 * 1024
)  # 100 MB default
ZIP_MAX_MEMBERS = getattr(settings, "FIRMWARE_ZIP_MAX_MEMBERS", 200)
ZIP_MAX_TOTAL_BYTES = getattr(
    settings, "FIRMWARE_ZIP_MAX_TOTAL_BYTES", 500 * 1024 * 1024
)  # 500 MB default
AI_SEND_PASSWORD = getattr(settings, "FIRMWARE_AI_SEND_PASSWORD", False)


def calc_storage_path(brand_slug, model_slug, variant_slug, category, fw_id):
    base = Path(
        getattr(settings, "FIRMWARE_STORAGE_ROOT", settings.BASE_DIR / "storage")
    )
    return base / brand_slug / model_slug / variant_slug / category / str(fw_id)


def _safe_filename(name: str) -> str:
    # Prevent path traversal and control length
    return Path(name).name[:255]


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
    if package_format not in FirmwarePackageFormat.values:
        raise ValueError("Unsupported firmware package format.")

    # Guard against oversized uploads to prevent resource exhaustion
    size = getattr(file_obj, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Upload exceeds maximum allowed size of {MAX_UPLOAD_BYTES} bytes"
        )

    tmp_root = Path(
        getattr(settings, "FIRMWARE_STORAGE_ROOT", settings.BASE_DIR / "storage")
    )
    tmp_dir = tmp_root / "pending" / str(uuid.uuid4())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(file_obj.name)
    file_path = tmp_dir / safe_name
    with open(file_path, "wb") as f:
        f.writelines(file_obj.chunks())

    pw_token = (
        encrypt_password(password) if (is_password_protected and password) else ""
    )
    return PendingFirmware.objects.create(
        original_file_name=safe_name,
        stored_file_path=str(file_path),
        uploader=uploader,
        uploaded_brand=uploaded_brand,
        uploaded_model=uploaded_model,
        uploaded_variant=uploaded_variant,
        package_format=package_format,
        is_password_protected=is_password_protected,
        encrypted_password=pw_token,
        metadata={"extra_info": extra_info} if extra_info else {},
    )


def attempt_extraction(pf: PendingFirmware) -> None:
    if not pf.is_password_protected:
        pf.extraction_status = "success"
        pf.save(update_fields=["extraction_status"])
        return
    try:
        password = pf.encrypted_password and decrypt_password(pf.encrypted_password)
    except Exception:
        pf.password_validation_status = "invalid"
        pf.extraction_status = "failed"
        pf.save(update_fields=["password_validation_status", "extraction_status"])
        return
    try:
        if zipfile.is_zipfile(pf.stored_file_path):
            dest_dir = Path(pf.stored_file_path).parent
            with zipfile.ZipFile(pf.stored_file_path) as zf:
                total_bytes = 0
                member_count = 0
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    member_count += 1
                    if member_count > ZIP_MAX_MEMBERS:
                        raise ValueError("Archive has too many entries")
                    total_bytes += member.file_size
                    if (
                        total_bytes > ZIP_MAX_TOTAL_BYTES
                        or member.file_size > ZIP_MAX_TOTAL_BYTES
                    ):
                        raise ValueError("Archive exceeds allowed uncompressed size")
                    target_path = dest_dir / member.filename
                    # Prevent path traversal
                    if not str(target_path.resolve()).startswith(
                        str(dest_dir.resolve())
                    ):
                        continue
                    zf.extract(member, dest_dir, pwd=password.encode())
                    # FINDING-0017: Reject symlinks created by ZIP extraction.
                    # resolve() check above passes for symlinks inside dest_dir,
                    # but they can redirect writes to arbitrary paths.
                    extracted = dest_dir / member.filename
                    if extracted.is_symlink():
                        extracted.unlink()
                        raise ValueError(
                            "Symlink detected in archive — extraction rejected for security."
                        )
            pf.password_validation_status = "valid"
            pf.extraction_status = "success"
        else:
            pf.password_validation_status = "unknown"
            pf.extraction_status = "pending"
    except Exception:
        pf.password_validation_status = "invalid"
        pf.extraction_status = "failed"
    pf.save(update_fields=["password_validation_status", "extraction_status"])


def _analyze_firmware(file_path: str, *, password: str | None = None) -> dict[str, Any]:
    """AI-powered firmware analysis — delegates to CoreAiService."""
    from apps.core.ai import CoreAiService

    prompt = (
        f"Analyze the firmware file at '{file_path}'. "
        "Extract brand, model, variant, category (official/engineering/"
        "readback/modified/other), subtype, chipset, and partition list."
    )
    if password:
        prompt += " The archive is password-protected."
    result = CoreAiService().generate_text(prompt)
    logger.info("AI analysis completed for %s", file_path)
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("AI firmware analysis did not return valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("AI firmware analysis must return a JSON object.")
    return data


def run_ai_analysis(pf: PendingFirmware) -> None:
    pw = (
        decrypt_password(pf.encrypted_password)
        if (AI_SEND_PASSWORD and pf.encrypted_password)
        else None
    )
    try:
        result = _analyze_firmware(pf.stored_file_path, password=pw)
    except Exception:
        logger.exception("AI analysis failed for pending firmware %s", pf.pk)
        pf.metadata = {**pf.metadata, "ai_error": "analysis_failed"}
        pf.save(update_fields=["metadata"])
        return
    pf.ai_brand = result.get("brand", "") or ""
    pf.ai_model = result.get("model", "") or ""
    pf.ai_variant = result.get("variant", "") or ""
    pf.ai_category = result.get("category") or None
    pf.ai_subtype = result.get("subtype") or None
    pf.chipset = result.get("chipset", "") or ""
    pf.partitions = result.get("partitions", []) or []
    pf.metadata = {**pf.metadata, **(result.get("metadata") or {})}
    pf.save()


@transaction.atomic
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
    governance_case_id: int | None = None,
):
    if decision != "approved" or governance_case_id is None:
        raise PermissionDenied("Firmware routing requires an approved governance case.")
    from apps.governance.models import ReviewCase

    content_type = ContentType.objects.get_for_model(pf, for_concrete_model=False)
    case = ReviewCase.objects.filter(
        pk=governance_case_id,
        content_type=content_type,
        object_id=str(pf.pk),
        status=ReviewCase.Status.APPROVED,
        is_open=True,
    ).first()
    if case is None:
        raise PermissionDenied("Firmware routing requires an approved governance case.")
    if admin_user.pk in {
        case.requested_by_id,
        case.reviewer_id,
    } or not admin_user.has_perm("governance.can_publish_content"):
        raise PermissionDenied("A separate authorized publisher is required.")

    package_format = package_format or pf.package_format
    if package_format not in FirmwarePackageFormat.values:
        raise ValidationError("Unsupported firmware package format.")

    pf.admin_decision = decision
    pf.admin_notes = notes

    target_brand = brand or pf.uploaded_brand
    target_model = model or pf.uploaded_model
    target_variant = variant or pf.uploaded_variant
    final_cat = category or pf.ai_category or "unclassified"

    base_kwargs = {
        "original_file_name": pf.original_file_name,
        "stored_file_path": "",
        "uploader": pf.uploader,
        "brand": target_brand,
        "model": target_model,
        "variant": target_variant,
        "package_format": package_format,
        "chipset": pf.chipset,
        "partitions": pf.partitions,
        "is_password_protected": pf.is_password_protected,
        "encrypted_password": pf.encrypted_password,
        "metadata": pf.metadata,
    }

    brand_slug = target_brand.slug if target_brand else "unknown"
    model_slug = target_model.slug if target_model else "unknown"
    variant_slug = target_variant.slug if target_variant else "unknown"
    target_dir = calc_storage_path(
        brand_slug, model_slug, variant_slug, final_cat, pf.id
    )
    os.makedirs(target_dir, exist_ok=True)
    final_path = Path(target_dir) / _safe_filename(pf.original_file_name)
    source_path = Path(pf.stored_file_path)
    moved_here = False
    if source_path.is_file():
        if final_path.exists():
            raise ValidationError("The firmware destination already exists.")
        shutil.move(str(source_path), str(final_path))
        moved_here = True
    elif not final_path.is_file():
        raise ValidationError("The staged firmware file is missing.")
    base_kwargs["stored_file_path"] = str(final_path)

    try:
        with transaction.atomic():
            if final_cat == "official":
                rec = OfficialFirmware.objects.create(**base_kwargs)
            elif final_cat == "engineering":
                rec = EngineeringFirmware.objects.create(
                    **base_kwargs, subtype=subtype or ""
                )
            elif final_cat == "readback":
                rec = ReadbackFirmware.objects.create(**base_kwargs)
            elif final_cat == "modified":
                rec = ModifiedFirmware.objects.create(
                    **base_kwargs, subtype=subtype or ""
                )
            elif final_cat == "other":
                rec = OtherFirmware.objects.create(**base_kwargs, subtype=subtype or "")
            else:
                rec = UnclassifiedFirmware.objects.create(
                    **base_kwargs, reason=unclassified_reason or "unclassified"
                )

            pf.category_locked = True
            pf.admin_decision = "approved"
            pf.admin_notes = notes
            pf.save(update_fields=["category_locked", "admin_decision", "admin_notes"])
    except Exception:
        if moved_here and final_path.is_file() and not source_path.exists():
            shutil.move(str(final_path), str(source_path))
        raise
    return rec
