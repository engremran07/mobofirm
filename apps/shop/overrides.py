"""Account-level quota overrides: grant, revoke and the audit trail.

The resolver (``entitlements.effective_entitlements``) reads the live override; this
module is how it is created and changed so every change leaves an audit row and the
per-request entitlement memo is cleared. The admin uses the same functions.
"""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from . import request_cache
from .models import QuotaOverrideAudit, UserQuotaOverride
from .services import OrderError, lock_user

LIMIT_FIELDS = (
    "limit_files",
    "limit_size",
    "size_unit",
    "window",
    "covers_free",
    "covers_featured",
    "covers_paid",
    "device_limit",
    "expires_at",
)


class OverrideError(OrderError):
    """The override could not be granted or changed."""


def snapshot(override: UserQuotaOverride) -> dict[str, Any]:
    """The JSON-safe state recorded in the audit row."""
    data: dict[str, Any] = {name: getattr(override, name) for name in LIMIT_FIELDS}
    data["expires_at"] = (
        override.expires_at.isoformat() if override.expires_at else None
    )
    data.update(
        reason=override.reason, is_active=override.is_active, summary=override.summary()
    )
    return data


def record(
    action: str, override: UserQuotaOverride, actor: Any | None
) -> QuotaOverrideAudit:
    return QuotaOverrideAudit.objects.create(
        user_id=override.user_id,
        action=action,
        actor=actor if getattr(actor, "pk", None) else None,
        reason=override.reason,
        snapshot=snapshot(override),
    )


def retire_expired(user: Any) -> int:
    """Switch off this user's expired overrides so a new one can take the slot."""
    expired = UserQuotaOverride.objects.filter(
        user=user, is_active=True, expires_at__lte=timezone.now()
    )
    count = 0
    for override in expired:
        override.is_active = False
        override.save(update_fields=["is_active", "updated_at"])
        record(QuotaOverrideAudit.Action.REVOKED, override, None)
        count += 1
    return count


@transaction.atomic
def grant_override(
    user: Any, *, actor: Any, reason: str, **limits: Any
) -> UserQuotaOverride:
    """Create the account override; refuses a second live one."""
    unknown = set(limits) - set(LIMIT_FIELDS)
    if unknown:
        raise OverrideError(f"Unknown override fields: {', '.join(sorted(unknown))}.")
    lock_user(user)
    retire_expired(user)
    if UserQuotaOverride.objects.filter(user=user, is_active=True).exists():
        raise OverrideError("This account already has an active override.")
    override = UserQuotaOverride(user=user, reason=reason, granted_by=actor, **limits)
    override.full_clean(exclude=["granted_by", "user"])
    override.save()
    record(QuotaOverrideAudit.Action.GRANTED, override, actor)
    request_cache.invalidate()
    return override


@transaction.atomic
def revoke_override(override: UserQuotaOverride, *, actor: Any) -> UserQuotaOverride:
    """Stop the override applying; the record and its audit stay."""
    if not override.is_active:
        return override
    override.is_active = False
    override.save(update_fields=["is_active", "updated_at"])
    record(QuotaOverrideAudit.Action.REVOKED, override, actor)
    request_cache.invalidate()
    return override


def audit_saved(override: UserQuotaOverride, *, actor: Any, created: bool) -> None:
    """Admin hook after a save: audit it and clear the entitlement memo."""
    if created:
        action = QuotaOverrideAudit.Action.GRANTED
    elif not override.is_active:
        action = QuotaOverrideAudit.Action.REVOKED
    else:
        action = QuotaOverrideAudit.Action.CHANGED
    record(action, override, actor)
    request_cache.invalidate()


def audit_deleted(override: UserQuotaOverride, *, actor: Any) -> None:
    record(QuotaOverrideAudit.Action.DELETED, override, actor)
    request_cache.invalidate()
