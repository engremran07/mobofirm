from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ReviewCase, ReviewEvent

SUPPORTED_MODELS = {
    ("blog", "post"),
    ("comments", "comment"),
    ("firmwares", "pendingfirmware"),
    ("tags", "tag"),
}
MAX_RATIONALE_LENGTH = 1000


class GovernanceError(Exception):
    pass


def _validate_key(key: uuid.UUID | str | None) -> uuid.UUID:
    if key is None:
        return uuid.uuid4()
    try:
        return key if isinstance(key, uuid.UUID) else uuid.UUID(str(key))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("Idempotency key must be a UUID.") from exc


def _validate_rationale(rationale: str, *, required: bool = True) -> str:
    normalized = (rationale or "").strip()
    if required and not normalized:
        raise ValidationError("A rationale is required for this governance action.")
    if len(normalized) > MAX_RATIONALE_LENGTH:
        raise ValidationError(
            f"Rationale must not exceed {MAX_RATIONALE_LENGTH} characters."
        )
    return normalized


def _require_permission(actor, permission: str) -> None:
    if not getattr(actor, "is_authenticated", False):
        raise PermissionDenied("Authentication is required.")
    if not actor.has_perm(f"governance.{permission}"):
        raise PermissionDenied("You do not have permission for this action.")


def _content_type_for(obj) -> ContentType:
    content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
    if (content_type.app_label, content_type.model) not in SUPPORTED_MODELS:
        raise ValidationError("This content type is not governed by this workflow.")
    return content_type


def _get_locked_target(case: ReviewCase):
    model = case.content_type.model_class()
    if model is None:
        raise GovernanceError("The case's content model is no longer installed.")
    try:
        return model._default_manager.select_for_update().get(pk=case.object_id)
    except model.DoesNotExist as exc:
        raise GovernanceError(
            "The content item for this case no longer exists."
        ) from exc


def _validate_target(obj) -> None:
    model = (obj._meta.app_label, obj._meta.model_name)
    if model == ("blog", "post"):
        if not obj.title.strip() or not obj.body.strip():
            raise ValidationError("A post requires a title and body before review.")
        if obj.status not in {"draft", "in_review"}:
            raise ValidationError("Only draft posts can enter review.")
    elif model == ("comments", "comment"):
        if obj.is_deleted:
            raise ValidationError("Deleted comments cannot enter review.")
        obj.full_clean()
    elif model == ("firmwares", "pendingfirmware"):
        if obj.admin_decision != "pending" or obj.category_locked:
            raise ValidationError("This firmware upload is no longer pending review.")
        if not obj.original_file_name or not obj.stored_file_path:
            raise ValidationError("Firmware upload metadata is incomplete.")
    elif model == ("tags", "tag"):
        if obj.is_deleted or obj.merge_into_id:
            raise ValidationError("Deleted or merged tags cannot enter review.")
        if not obj.name.strip():
            raise ValidationError("A tag requires a name before review.")
    else:
        raise ValidationError("This content type is not governed by this workflow.")


def _validate_submitter(obj, actor) -> None:
    if actor is None or getattr(actor, "is_staff", False):
        return
    owner_field = {
        ("blog", "post"): "author_id",
        ("comments", "comment"): "user_id",
        ("firmwares", "pendingfirmware"): "uploader_id",
    }.get((obj._meta.app_label, obj._meta.model_name))
    if owner_field and getattr(obj, owner_field) != actor.pk:
        raise PermissionDenied("Only the content owner or staff may submit this item.")


def _prepare_target_for_review(obj) -> None:
    model = (obj._meta.app_label, obj._meta.model_name)
    if model == ("blog", "post"):
        obj.status = "in_review"
        obj.save(update_fields=["status", "is_published", "updated_at"])
    elif model == ("comments", "comment"):
        if obj.status not in {"spam", "rejected"}:
            obj.status = "pending"
        obj.is_approved = False
        obj.save(update_fields=["status", "is_approved", "updated_at"])


def _append_event(
    *,
    case: ReviewCase,
    actor,
    action: str,
    from_status: str,
    to_status: str,
    rationale: str = "",
    metadata: dict[str, Any] | None = None,
    idempotency_key: uuid.UUID,
) -> ReviewEvent:
    existing = ReviewEvent.objects.filter(
        case=case, idempotency_key=idempotency_key
    ).first()
    if existing:
        if existing.action != action or existing.actor_id != getattr(actor, "pk", None):
            raise GovernanceError(
                "Idempotency key was already used for another action."
            )
        return existing
    return ReviewEvent.objects.create(
        case=case,
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=to_status,
        rationale=rationale,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )


def submit_for_review(obj, actor=None) -> ReviewCase:
    content_type = _content_type_for(obj)
    _validate_target(obj)
    _validate_submitter(obj, actor)
    with transaction.atomic():
        existing = (
            ReviewCase.objects.select_for_update()
            .filter(
                content_type=content_type,
                object_id=str(obj.pk),
                is_open=True,
            )
            .first()
        )
        if existing:
            if existing.status == ReviewCase.Status.SUBMITTED:
                return existing
            if existing.status not in {
                ReviewCase.Status.CHANGES_REQUESTED,
                ReviewCase.Status.CORRECTION_REQUIRED,
            } or existing.requested_by_id != getattr(actor, "pk", None):
                raise GovernanceError("This item already has an active review case.")
            previous = existing.status
            existing.status = ReviewCase.Status.SUBMITTED
            existing.reviewer = None
            existing.decision_data = {}
            existing.revision += 1
            existing.save(
                update_fields=[
                    "status",
                    "reviewer",
                    "decision_data",
                    "revision",
                    "updated_at",
                ]
            )
            _append_event(
                case=existing,
                actor=actor,
                action=ReviewEvent.Action.RESUBMITTED,
                from_status=previous,
                to_status=existing.status,
                metadata={"revision": existing.revision},
                idempotency_key=uuid.uuid4(),
            )
            _prepare_target_for_review(obj)
            return existing

        try:
            with transaction.atomic():
                case = ReviewCase.objects.create(
                    content_type=content_type,
                    object_id=str(obj.pk),
                    requested_by=actor,
                )
        except IntegrityError:
            concurrent = ReviewCase.objects.filter(
                content_type=content_type,
                object_id=str(obj.pk),
                is_open=True,
            ).first()
            if concurrent:
                return concurrent
            raise

        _prepare_target_for_review(obj)
        _append_event(
            case=case,
            actor=actor,
            action=ReviewEvent.Action.SUBMITTED,
            from_status="",
            to_status=case.status,
            idempotency_key=uuid.uuid4(),
        )
        return case


def _locked_case(case_id: int) -> ReviewCase:
    return (
        ReviewCase.objects.select_for_update()
        .select_related("content_type", "result_content_type")
        .get(pk=case_id)
    )


def _find_duplicate(case: ReviewCase, actor, key: uuid.UUID, action: str):
    event = ReviewEvent.objects.filter(case=case, idempotency_key=key).first()
    if not event:
        return None
    if event.action != action or event.actor_id != getattr(actor, "pk", None):
        raise GovernanceError("Idempotency key was already used for another action.")
    return case


def start_review(
    case_id: int,
    actor,
    *,
    rationale: str = "",
    idempotency_key: uuid.UUID | str | None = None,
) -> ReviewCase:
    _require_permission(actor, "can_review_content")
    key = _validate_key(idempotency_key)
    note = _validate_rationale(rationale, required=False)
    with transaction.atomic():
        case = _locked_case(case_id)
        if duplicate := _find_duplicate(
            case, actor, key, ReviewEvent.Action.REVIEW_STARTED
        ):
            return duplicate
        if case.status != ReviewCase.Status.SUBMITTED:
            raise GovernanceError("Only submitted cases can enter review.")
        if case.requested_by_id == actor.pk:
            raise PermissionDenied("A submitter cannot review their own content.")
        previous = case.status
        case.status = ReviewCase.Status.IN_REVIEW
        case.reviewer = actor
        case.save(update_fields=["status", "reviewer", "updated_at"])
        _append_event(
            case=case,
            actor=actor,
            action=ReviewEvent.Action.REVIEW_STARTED,
            from_status=previous,
            to_status=case.status,
            rationale=note,
            idempotency_key=key,
        )
        return case


def _validate_firmware_decision(data: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "category",
        "subtype",
        "brand_id",
        "model_id",
        "variant_id",
        "unclassified_reason",
        "package_format",
    }
    if set(data) - allowed:
        raise ValidationError("Firmware decision contains unsupported fields.")
    from apps.firmwares.models import Brand, FirmwarePackageFormat, Model, Variant

    result = dict(data)
    brand = Brand.objects.filter(pk=result.get("brand_id")).first()
    model = Model.objects.filter(pk=result.get("model_id")).first()
    variant = Variant.objects.filter(pk=result.get("variant_id")).first()
    if model and brand and model.brand_id != brand.pk:
        raise ValidationError("The selected model does not belong to the brand.")
    if model and variant and variant.model_id != model.pk:
        raise ValidationError("The selected variant does not belong to the model.")
    if variant and model is None:
        raise ValidationError("A model must be selected when a variant is selected.")
    category = result.get("category")
    if category and category not in {
        "official",
        "engineering",
        "readback",
        "modified",
        "other",
        "unclassified",
    }:
        raise ValidationError("Unsupported firmware category.")
    package_format = result.get("package_format")
    if package_format and package_format not in FirmwarePackageFormat.values:
        raise ValidationError("Unsupported firmware package format.")
    return result


def decide_case(
    case_id: int,
    actor,
    decision: str,
    rationale: str,
    *,
    decision_data: dict[str, Any] | None = None,
    idempotency_key: uuid.UUID | str | None = None,
) -> ReviewCase:
    _require_permission(actor, "can_review_content")
    if decision not in {"approve", "request_changes", "reject"}:
        raise ValidationError("Decision must be approve, request_changes, or reject.")
    if decision_data is not None and not isinstance(decision_data, dict):
        raise ValidationError("Decision data must be a JSON object.")
    key = _validate_key(idempotency_key)
    note = _validate_rationale(rationale)
    event_action = {
        "approve": ReviewEvent.Action.APPROVED,
        "request_changes": ReviewEvent.Action.CHANGES_REQUESTED,
        "reject": ReviewEvent.Action.REJECTED,
    }[decision]
    with transaction.atomic():
        case = _locked_case(case_id)
        if duplicate := _find_duplicate(case, actor, key, event_action):
            return duplicate
        if case.status != ReviewCase.Status.IN_REVIEW:
            raise GovernanceError("Only cases in review can receive a decision.")
        if case.reviewer_id != actor.pk:
            raise PermissionDenied("Only the assigned reviewer may decide this case.")
        if decision == "approve":
            clean_data = decision_data or {}
            if case.content_type.app_label == "firmwares":
                clean_data = _validate_firmware_decision(clean_data)
            elif clean_data:
                raise ValidationError(
                    "This content type does not accept decision data."
                )
            case.decision_data = clean_data
            case.status = ReviewCase.Status.APPROVED
            case.is_open = True
        elif decision == "request_changes":
            case.status = ReviewCase.Status.CHANGES_REQUESTED
            case.is_open = True
        else:
            case.status = ReviewCase.Status.REJECTED
            case.is_open = False
            _reject_target(_get_locked_target(case), case)
        previous = ReviewCase.Status.IN_REVIEW
        case.save(update_fields=["status", "is_open", "decision_data", "updated_at"])
        _append_event(
            case=case,
            actor=actor,
            action=event_action,
            from_status=previous,
            to_status=case.status,
            rationale=note,
            metadata=case.decision_data if decision == "approve" else {},
            idempotency_key=key,
        )
        return case


def _reject_target(obj, case: ReviewCase) -> None:
    model = (obj._meta.app_label, obj._meta.model_name)
    if model == ("blog", "post"):
        obj.status = "draft"
        obj.save(update_fields=["status", "is_published", "updated_at"])
    elif model == ("comments", "comment"):
        obj.status = "rejected"
        obj.is_approved = False
        obj.save(update_fields=["status", "is_approved", "updated_at"])
    elif model == ("firmwares", "pendingfirmware"):
        obj.admin_decision = "rejected"
        obj.admin_notes = (
            f"Governance case {case.pk} rejected by its assigned reviewer."
        )
        obj.save(update_fields=["admin_decision", "admin_notes"])


def _fulfill_target(case: ReviewCase, actor, scheduled_at: datetime | None):
    obj = _get_locked_target(case)
    model = (case.content_type.app_label, case.content_type.model)
    if model == ("blog", "post"):
        from apps.blog.models import PostStatus

        now = timezone.now()
        if scheduled_at and timezone.is_naive(scheduled_at):
            raise ValidationError("Scheduled publication time must include a timezone.")
        obj.status = PostStatus.SCHEDULED if scheduled_at else PostStatus.PUBLISHED
        obj.publish_at = scheduled_at or obj.publish_at or now
        if not scheduled_at:
            obj.published_at = obj.published_at or now
        obj.save(
            update_fields=[
                "status",
                "publish_at",
                "published_at",
                "is_published",
                "updated_at",
            ]
        )
        return obj
    if scheduled_at:
        raise ValidationError("Only blog posts can be scheduled.")
    if model == ("comments", "comment"):
        obj.status = "approved"
        obj.is_approved = True
        obj.save(update_fields=["status", "is_approved", "updated_at"])
        return obj
    if model == ("tags", "tag"):
        obj.is_curated = True
        obj.save(update_fields=["is_curated", "updated_at"])
        return obj
    if model == ("firmwares", "pendingfirmware"):
        from apps.firmwares.models import Brand, Model, Variant
        from apps.firmwares.services import moderate_and_route

        data = case.decision_data
        brand = Brand.objects.filter(pk=data.get("brand_id")).first()
        firmware_model = Model.objects.filter(pk=data.get("model_id")).first()
        variant = Variant.objects.filter(pk=data.get("variant_id")).first()
        result = moderate_and_route(
            obj,
            "approved",
            admin_user=actor,
            category=data.get("category"),
            subtype=data.get("subtype"),
            brand=brand,
            model=firmware_model,
            variant=variant,
            notes=case.events.filter(action=ReviewEvent.Action.APPROVED)
            .order_by("-occurred_at")
            .values_list("rationale", flat=True)
            .first()
            or "",
            unclassified_reason=data.get("unclassified_reason", ""),
            package_format=data.get("package_format"),
            governance_case_id=case.pk,
        )
        if result is None:
            raise GovernanceError(
                "Firmware approval did not create a published record."
            )
        return result
    raise GovernanceError("No fulfillment policy exists for this content type.")


def fulfill_case(
    case_id: int,
    actor,
    *,
    scheduled_at: datetime | None = None,
    idempotency_key: uuid.UUID | str | None = None,
) -> ReviewCase:
    _require_permission(actor, "can_publish_content")
    key = _validate_key(idempotency_key)
    with transaction.atomic():
        case = _locked_case(case_id)
        if duplicate := _find_duplicate(case, actor, key, ReviewEvent.Action.FULFILLED):
            return duplicate
        if case.status != ReviewCase.Status.APPROVED:
            raise GovernanceError("Only approved cases can be fulfilled.")
        if actor.pk in {case.requested_by_id, case.reviewer_id}:
            raise PermissionDenied(
                "The submitter and reviewer cannot publish the same case."
            )
        result = _fulfill_target(case, actor, scheduled_at)
        case.result_content_type = ContentType.objects.get_for_model(
            result, for_concrete_model=False
        )
        case.result_object_id = str(result.pk)
        case.publisher = actor
        case.status = ReviewCase.Status.COMPLETED
        case.is_open = False
        case.save(
            update_fields=[
                "result_content_type",
                "result_object_id",
                "publisher",
                "status",
                "is_open",
                "updated_at",
            ]
        )
        _append_event(
            case=case,
            actor=actor,
            action=ReviewEvent.Action.FULFILLED,
            from_status=ReviewCase.Status.APPROVED,
            to_status=case.status,
            metadata={"result_model": case.result_content_type.model},
            idempotency_key=key,
        )
        return case


def report_issue(
    case_id: int,
    actor,
    rationale: str,
    *,
    idempotency_key: uuid.UUID | str | None = None,
) -> ReviewCase:
    _require_permission(actor, "can_review_content")
    key = _validate_key(idempotency_key)
    note = _validate_rationale(rationale)
    with transaction.atomic():
        case = _locked_case(case_id)
        if duplicate := _find_duplicate(
            case, actor, key, ReviewEvent.Action.CORRECTION_REQUIRED
        ):
            return duplicate
        if case.status != ReviewCase.Status.COMPLETED:
            raise GovernanceError(
                "Only completed cases can receive corrective actions."
            )
        result = case.result_object
        if result is None:
            raise GovernanceError("The fulfilled content is no longer available.")
        model = (case.result_content_type.app_label, case.result_content_type.model)
        if model == ("blog", "post"):
            result.status = "in_review"
            result.save(update_fields=["status", "is_published", "updated_at"])
        elif model == ("comments", "comment"):
            result.status = "pending"
            result.is_approved = False
            result.save(update_fields=["status", "is_approved", "updated_at"])
        elif model == ("tags", "tag"):
            result.is_curated = False
            result.save(update_fields=["is_curated", "updated_at"])
        elif model[0] == "firmwares" and hasattr(result, "is_active"):
            result.is_active = False
            result.save(update_fields=["is_active", "updated_at"])
        else:
            raise GovernanceError(
                "No corrective-action policy exists for this content."
            )
        previous = case.status
        case.status = ReviewCase.Status.CORRECTION_REQUIRED
        case.is_open = True
        case.reviewer = None
        case.save(update_fields=["status", "is_open", "reviewer", "updated_at"])
        _append_event(
            case=case,
            actor=actor,
            action=ReviewEvent.Action.CORRECTION_REQUIRED,
            from_status=previous,
            to_status=case.status,
            rationale=note,
            idempotency_key=key,
        )
        return case
