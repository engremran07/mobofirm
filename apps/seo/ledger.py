"""Change ledger: before/after records for every SEO change, with verified apply
and conflict-checked rollback.

Only models listed in ``TARGETS`` can be changed through the ledger. ``UPDATE``
entries store just the changed fields; ``CREATE``/``DELETE`` store full rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from .models import ChangeLedger, Metadata, Redirect, SEOSettings

TARGETS: dict[str, type[models.Model]] = {
    "seo.redirect": Redirect,
    "seo.metadata": Metadata,
    "seo.seosettings": SEOSettings,
}
_VOLATILE = frozenset({"created_at", "updated_at"})
_EXPECTED_ERRORS = (ValidationError, ObjectDoesNotExist, IntegrityError)


class LedgerError(Exception):
    """A change could not be applied, verified or rolled back."""


def model_label(model: type[models.Model]) -> str:
    return f"{model._meta.app_label}.{model._meta.model_name}"


def _plain(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


def serialize(instance: models.Model) -> dict[str, Any]:
    return {
        field.attname: _plain(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
        if field.attname not in _VOLATILE
    }


def diff(
    before: dict[str, Any], after: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    keys = [key for key in after if before.get(key) != after.get(key)]
    return {key: before.get(key) for key in keys}, {key: after[key] for key in keys}


def _model(label: str) -> type[models.Model]:
    try:
        return TARGETS[label]
    except KeyError:
        raise LedgerError(f"{label} cannot be changed through the ledger.") from None


def _matches(instance: models.Model, expected: dict[str, Any]) -> bool:
    current = serialize(instance)
    return all(current.get(key) == value for key, value in expected.items())


def _write(ledger: ChangeLedger, action: str, values: dict[str, Any]) -> None:
    model = _model(ledger.target_model)
    if action == ChangeLedger.Action.CREATE:
        instance = model(**values)
        instance.save()
        ledger.target_pk = str(instance.pk)
    elif action == ChangeLedger.Action.UPDATE:
        instance = model._default_manager.get(pk=ledger.target_pk)
        for key, value in values.items():
            setattr(instance, key, value)
        instance.save()
    else:
        model._default_manager.filter(pk=ledger.target_pk).delete()


def _verify(ledger: ChangeLedger, action: str, values: dict[str, Any]) -> None:
    model = _model(ledger.target_model)
    stored = model._default_manager.filter(pk=ledger.target_pk).first()
    if action == ChangeLedger.Action.DELETE:
        if stored is not None:
            raise LedgerError("Row still exists after delete.")
        return
    if stored is None or not _matches(stored, values):
        raise LedgerError("Stored state does not match the intended change.")


def _fail(ledger: ChangeLedger, error: Exception) -> ChangeLedger:
    ledger.status = ChangeLedger.Status.FAILED
    ledger.error = str(error)[:2000]
    ledger.save()
    return ledger


def apply_change(ledger: ChangeLedger, user: Any = None) -> ChangeLedger:
    """Write the change, verify what was stored, then mark it applied."""
    if ledger.status not in {
        ChangeLedger.Status.PROPOSED,
        ChangeLedger.Status.APPROVED,
    }:
        raise LedgerError(f"Cannot apply a change that is {ledger.status}.")
    try:
        with transaction.atomic():
            _write(ledger, ledger.action, ledger.after)
            _verify(ledger, ledger.action, ledger.after)
    except (*_EXPECTED_ERRORS, LedgerError) as error:
        return _fail(ledger, error)
    now = timezone.now()
    ledger.status = ChangeLedger.Status.APPLIED
    ledger.applied_at = ledger.verified_at = now
    ledger.error = ""
    if user is not None and getattr(user, "is_authenticated", False):
        ledger.decided_by = user
    ledger.save()
    return ledger


def approve(ledger: ChangeLedger, user: Any) -> ChangeLedger:
    if ledger.status != ChangeLedger.Status.PROPOSED:
        raise LedgerError(f"Only proposed changes can be approved ({ledger.status}).")
    ledger.status = ChangeLedger.Status.APPROVED
    ledger.decided_by = user
    ledger.decided_at = timezone.now()
    ledger.save()
    return apply_change(ledger, user)


def reject(ledger: ChangeLedger, user: Any) -> ChangeLedger:
    if ledger.status != ChangeLedger.Status.PROPOSED:
        raise LedgerError(f"Only proposed changes can be rejected ({ledger.status}).")
    ledger.status = ChangeLedger.Status.REJECTED
    ledger.decided_by = user
    ledger.decided_at = timezone.now()
    ledger.save()
    return ledger


def rollback(ledger: ChangeLedger, user: Any) -> ChangeLedger:
    """Undo an applied change, refusing if the target changed since."""
    if ledger.status != ChangeLedger.Status.APPLIED:
        raise LedgerError(f"Only applied changes can be rolled back ({ledger.status}).")
    model = _model(ledger.target_model)
    inverse = {
        ChangeLedger.Action.CREATE: ChangeLedger.Action.DELETE,
        ChangeLedger.Action.UPDATE: ChangeLedger.Action.UPDATE,
        ChangeLedger.Action.DELETE: ChangeLedger.Action.CREATE,
    }[ChangeLedger.Action(ledger.action)]
    if ledger.action != ChangeLedger.Action.DELETE:
        current = model._default_manager.filter(pk=ledger.target_pk).first()
        changed_since = ledger.action == ChangeLedger.Action.UPDATE and (
            current is not None and not _matches(current, ledger.after)
        )
        if current is None or changed_since:
            raise LedgerError(
                "The target changed after this entry; roll back manually."
            )
    try:
        with transaction.atomic():
            _write(ledger, inverse, ledger.before)
            _verify(
                ledger,
                inverse,
                {} if inverse == ChangeLedger.Action.DELETE else ledger.before,
            )
    except (*_EXPECTED_ERRORS, LedgerError) as error:
        raise LedgerError(f"Rollback failed: {error}") from error
    ledger.status = ChangeLedger.Status.ROLLED_BACK
    ledger.rolled_back_at = timezone.now()
    ledger.decided_by = user
    ledger.save()
    return ledger


def record_manual(
    *,
    user: Any,
    action: str,
    instance: models.Model,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str = "Edited in the admin",
) -> ChangeLedger | None:
    """Ledger an edit that a person already made, so it can be rolled back."""
    label = model_label(type(instance))
    if label not in TARGETS:
        return None
    if action == ChangeLedger.Action.UPDATE:
        before, after = diff(before, after)
        if not after:
            return None
    now = timezone.now()
    return ChangeLedger.objects.create(
        idempotency_key=f"manual:{uuid.uuid4().hex}",
        kind="manual_edit",
        action=action,
        target_model=label,
        target_pk=str(instance.pk or ""),
        target_label=str(instance)[:255],
        before=before,
        after=after,
        reason=reason,
        source=f"user:{user.get_username()}",
        mode="manual",
        status=ChangeLedger.Status.APPLIED,
        decided_by=user if getattr(user, "is_authenticated", False) else None,
        decided_at=now,
        applied_at=now,
        verified_at=now,
    )
