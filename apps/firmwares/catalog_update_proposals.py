"""Member-submitted corrections to descriptive firmware catalog metadata."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.contrib.admin.models import CHANGE, LogEntry
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db import IntegrityError, models, transaction
from django.db.models import Model as DjangoModel
from django.utils import timezone

from .models import (
    Brand,
    CatalogUpdateProposal,
    Chipset,
    ChipsetVendor,
    FlashingGuideTemplate,
    FlashingTool,
    Model,
)
from .notifications import notify_users

MAX_REVIEW_REASON = 1000
MAX_PROPOSALS_PER_HOUR = 10

TARGET_CONFIG: dict[str, tuple[type[DjangoModel], str, tuple[str, ...]]] = {
    CatalogUpdateProposal.TargetKind.BRAND: (
        Brand,
        "brand",
        ("description",),
    ),
    CatalogUpdateProposal.TargetKind.CHIPSET_VENDOR: (
        ChipsetVendor,
        "chipset_vendor",
        ("aliases",),
    ),
    CatalogUpdateProposal.TargetKind.CHIPSET: (
        Chipset,
        "chipset",
        ("family", "description"),
    ),
    CatalogUpdateProposal.TargetKind.MODEL: (
        Model,
        "model",
        (
            "marketing_name",
            "also_known_as",
            "description",
            "cpu",
            "gpu",
            "network_technology",
            "os_version",
            "display_size",
            "battery",
            "dimensions",
            "weight",
        ),
    ),
    CatalogUpdateProposal.TargetKind.FLASHING_TOOL: (
        FlashingTool,
        "flashing_tool",
        ("description", "version"),
    ),
    CatalogUpdateProposal.TargetKind.GUIDE_TEMPLATE: (
        FlashingGuideTemplate,
        "guide_template",
        ("title_template", "summary_template"),
    ),
}


@dataclass(frozen=True)
class ProposalResult:
    proposal: CatalogUpdateProposal
    created: bool = True


def target_config(target_kind: str) -> tuple[type[DjangoModel], str, tuple[str, ...]]:
    try:
        return TARGET_CONFIG[target_kind]
    except KeyError as error:
        raise ValidationError("Choose a supported catalog item.") from error


def _verified_member(user: Any) -> bool:
    from .catalog_lookup import has_verified_email

    return bool(getattr(user, "is_active", False) and has_verified_email(user))


def _clean_values(
    target: DjangoModel, target_kind: str, values: Mapping[str, Any]
) -> dict[str, Any]:
    _, _, allowed_fields = target_config(target_kind)
    if not isinstance(values, Mapping) or not values:
        raise ValidationError("Provide at least one catalog correction.")
    if not set(values) <= set(allowed_fields):
        raise ValidationError("The proposal contains an unsupported field.")

    cleaned: dict[str, Any] = {}
    for name, value in values.items():
        if (
            target_kind == CatalogUpdateProposal.TargetKind.CHIPSET_VENDOR
            and name == "aliases"
        ):
            if isinstance(value, str):
                aliases = [" ".join(line.split()) for line in value.splitlines()]
                value = [alias for alias in aliases if alias]
            if (
                not isinstance(value, list)
                or len(value) > 30
                or any(
                    not isinstance(alias, str) or len(alias) > 100 for alias in value
                )
            ):
                raise ValidationError("Enter up to 30 chipset aliases, one per line.")
            value = list(dict.fromkeys(" ".join(alias.split()) for alias in value))
            if any(not alias for alias in value):
                raise ValidationError("Chipset aliases cannot be blank.")
        elif not isinstance(value, str):
            raise ValidationError(
                f"The proposed {name.replace('_', ' ')} must be text."
            )

        model_field = target._meta.get_field(name)
        if not isinstance(model_field, models.Field):
            raise ImproperlyConfigured(
                f"Catalog proposal field {name!r} is not a forward model field."
            )
        cleaned[name] = model_field.clean(value, target)
    return cleaned


def _require_target_reviewer(user: Any, model_class: type[DjangoModel]) -> None:
    permission = f"firmwares.change_{model_class.__name__.lower()}"
    if not (
        getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "is_staff", False)
        and user.has_perm(permission)
    ):
        raise PermissionDenied(
            "You do not have permission to review this catalog item."
        )


def submit(
    user: Any,
    *,
    target_kind: str,
    target_id: int,
    values: Mapping[str, Any],
) -> ProposalResult:
    """Create one pending correction per verified member and catalog item."""
    if not _verified_member(user):
        raise PermissionDenied("A verified account is required.")
    model_class, target_field, _ = target_config(target_kind)

    with transaction.atomic():
        manager = model_class._meta.default_manager
        if manager is None:
            raise ImproperlyConfigured(
                f"Catalog target {model_class.__name__} has no default manager."
            )
        target = manager.select_for_update().get(pk=target_id)
        cleaned = _clean_values(target, target_kind, values)
        base = {name: getattr(target, name) for name in cleaned}
        if all(base[name] == value for name, value in cleaned.items()):
            raise ValidationError("The proposal does not change this catalog item.")

        pending_filter = {
            target_field: target,
            "submitted_by": user,
            "status": CatalogUpdateProposal.Status.PENDING,
        }
        existing = CatalogUpdateProposal.objects.filter(**pending_filter).first()
        if existing is not None:
            if existing.base_values == base and existing.proposed_values == cleaned:
                return ProposalResult(existing, created=False)
            raise ValidationError(
                "You already have a pending proposal for this catalog item."
            )

        recent_count = CatalogUpdateProposal.objects.filter(
            submitted_by=user,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent_count >= MAX_PROPOSALS_PER_HOUR:
            raise ValidationError("You have reached the hourly catalog proposal limit.")

        try:
            with transaction.atomic():
                proposal = CatalogUpdateProposal.objects.create(
                    target_kind=target_kind,
                    **{target_field: target},
                    submitted_by=user,
                    base_values=base,
                    proposed_values=cleaned,
                )
        except IntegrityError:
            existing = CatalogUpdateProposal.objects.filter(**pending_filter).first()
            if existing is None:
                raise
            if existing.base_values == base and existing.proposed_values == cleaned:
                return ProposalResult(existing, created=False)
            raise ValidationError(
                "You already have a pending proposal for this catalog item."
            ) from None
        return ProposalResult(proposal)


def review(
    proposal_id: int,
    reviewer: Any,
    *,
    decision: str,
    reason: str = "",
    values: Mapping[str, Any] | None = None,
) -> CatalogUpdateProposal:
    """Approve corrected metadata or reject it; stale proposals never overwrite."""
    if decision not in {"approve", "reject"}:
        raise ValidationError("Choose approve or reject.")
    reason = " ".join(reason.split())
    if len(reason) > MAX_REVIEW_REASON:
        raise ValidationError(
            f"Keep the review reason within {MAX_REVIEW_REASON} characters."
        )
    if decision == "reject" and not reason:
        raise ValidationError(
            "Give the contributor a reason for rejecting the proposal."
        )

    with transaction.atomic():
        proposal = CatalogUpdateProposal.objects.select_for_update().get(pk=proposal_id)
        if proposal.status != CatalogUpdateProposal.Status.PENDING:
            raise ValidationError("This proposal has already been reviewed.")
        model_class, target_field, _ = target_config(proposal.target_kind)
        _require_target_reviewer(reviewer, model_class)
        target_id = getattr(proposal, f"{target_field}_id")
        manager = model_class._meta.default_manager
        if manager is None:
            raise ImproperlyConfigured(
                f"Catalog target {model_class.__name__} has no default manager."
            )
        target = manager.select_for_update().get(pk=target_id)

        if any(
            getattr(target, name) != old_value
            for name, old_value in proposal.base_values.items()
        ):
            proposal.status = CatalogUpdateProposal.Status.STALE
            proposal.reviewed_by = reviewer
            proposal.reviewed_at = timezone.now()
            proposal.review_reason = (
                "The catalog item changed after this proposal was submitted. "
                "Submit an updated proposal."
            )
            proposal.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "review_reason",
                    "updated_at",
                ]
            )
            _audit(proposal, reviewer, "Marked stale", proposal.review_reason)
            requester_id = proposal.submitted_by_id
            transaction.on_commit(
                lambda: notify_users(
                    [requester_id],
                    sender=CatalogUpdateProposal,
                    title="Catalog proposal needs an update",
                    message=proposal.review_reason,
                    priority="warning",
                    subject=proposal.pk,
                )
            )
            return proposal

        if decision == "reject":
            proposal.status = CatalogUpdateProposal.Status.REJECTED
        else:
            reviewed_values = _clean_values(
                target,
                proposal.target_kind,
                proposal.proposed_values if values is None else values,
            )
            if set(reviewed_values) != set(proposal.proposed_values):
                raise ValidationError(
                    "Keep the reviewed fields aligned with the proposal."
                )
            for name, value in reviewed_values.items():
                setattr(target, name, value)
            try:
                with transaction.atomic():
                    target.save(update_fields=[*reviewed_values, "updated_at"])
            except IntegrityError as error:
                raise ValidationError(
                    "The corrected catalog values conflict with an existing record."
                ) from error
            LogEntry.objects.log_actions(
                reviewer.pk,
                [target],
                CHANGE,
                f"Approved catalog update proposal #{proposal.pk}.",
                single_object=True,
            )
            if proposal.target_kind == CatalogUpdateProposal.TargetKind.BRAND:
                from .signal_handlers import schedule_blog_sync

                schedule_blog_sync(brand_ids={target.pk})
            proposal.proposed_values = reviewed_values
            proposal.status = CatalogUpdateProposal.Status.APPROVED

        proposal.reviewed_by = reviewer
        proposal.reviewed_at = timezone.now()
        proposal.review_reason = reason
        proposal.save(
            update_fields=[
                "proposed_values",
                "status",
                "reviewed_by",
                "reviewed_at",
                "review_reason",
                "updated_at",
            ]
        )
        _audit(proposal, reviewer, decision.capitalize(), reason)
        requester_id = proposal.submitted_by_id
        status_text = "approved" if decision == "approve" else "rejected"
        transaction.on_commit(
            lambda: notify_users(
                [requester_id],
                sender=CatalogUpdateProposal,
                title=f"Catalog proposal {status_text}",
                message=f"Your catalog update proposal was {status_text}. {reason}".strip(),
                priority="info" if decision == "approve" else "warning",
                subject=proposal.pk,
            )
        )
        return proposal


def _audit(
    proposal: CatalogUpdateProposal,
    reviewer: Any,
    action: str,
    reason: str,
) -> None:
    detail = f" Reason: {reason}" if reason else ""
    LogEntry.objects.log_actions(
        reviewer.pk,
        [proposal],
        CHANGE,
        f"{action} catalog update proposal #{proposal.pk}.{detail}",
        single_object=True,
    )
