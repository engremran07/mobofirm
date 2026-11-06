"""Autonomy policy: which changes apply themselves and which wait for a person.

Modes (``SEOSettings.autonomy_mode``):

* ``off`` - nothing is recorded or applied.
* ``suggest`` - every change is recorded as a proposal; nothing is applied.
* ``safe_autopilot`` - low-risk, reversible changes apply and are verified; all
  other changes wait for approval.
* ``full`` - low and high risk changes apply; protected policies still wait.

Protected policies (robots/AI crawler policy, global noindex, canonical policy,
integration switches, the autonomy mode itself) can never be applied without a
person, in any mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ledger as ledger_service
from .models import ChangeLedger, Issue, Redirect, SEOSettings
from .redirects import flattened_target, validate_redirect

LOW, HIGH, PROTECTED = "low", "high", "protected"

KIND_RISK: dict[str, str] = {
    "redirect_create": LOW,
    "redirect_flatten": LOW,
    "redirect_retarget": HIGH,
    "redirect_delete": HIGH,
    "settings_change": PROTECTED,
}


@dataclass(frozen=True)
class Decision:
    record: bool
    apply: bool


def decide(kind: str, mode: str) -> Decision:
    """Unknown kinds are treated as protected."""
    risk = KIND_RISK.get(kind, PROTECTED)
    if mode == SEOSettings.AutonomyMode.OFF:
        return Decision(record=False, apply=False)
    if mode == SEOSettings.AutonomyMode.SAFE_AUTOPILOT:
        return Decision(record=True, apply=risk == LOW)
    if mode == SEOSettings.AutonomyMode.FULL:
        return Decision(record=True, apply=risk in {LOW, HIGH})
    return Decision(record=True, apply=False)


def _validate(
    target_model: str, action: str, after: dict[str, Any], pk: str
) -> list[str]:
    if target_model != "seo.redirect" or action == ChangeLedger.Action.DELETE:
        return []
    exclude = int(pk) if pk.isdigit() else None
    existing = Redirect.objects.filter(pk=exclude).first() if exclude else None
    source = after.get("source") or (existing.source if existing else "")
    target = after.get("target") or (existing.target if existing else "")
    return validate_redirect(source, target, exclude_pk=exclude)


def submit_change(
    *,
    kind: str,
    action: str,
    target_model: str,
    target_pk: str = "",
    target_label: str = "",
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str,
    source: str,
    idempotency_key: str,
) -> ChangeLedger | None:
    """Record a change and apply it only if policy allows. Idempotent by key."""
    row = SEOSettings.get_solo()
    if not row.seo_enabled:
        return None
    decision = decide(kind, row.autonomy_mode)
    if not decision.record:
        return None
    ledger, created = ChangeLedger.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "kind": kind,
            "action": action,
            "target_model": target_model,
            "target_pk": target_pk,
            "target_label": target_label[:255],
            "before": before,
            "after": after,
            "reason": reason[:500],
            "source": source,
            "mode": row.autonomy_mode,
            "requires_approval": not decision.apply,
        },
    )
    if not created:
        return ledger
    errors = _validate(target_model, action, after, target_pk)
    ledger.validation = {"errors": errors}
    if errors:
        ledger.status = ChangeLedger.Status.FAILED
        ledger.error = "; ".join(errors)
        ledger.save()
        return ledger
    ledger.save()
    if decision.apply:
        return ledger_service.apply_change(ledger)
    return ledger


def propose_redirect(
    old_path: str, new_path: str, *, source: str, reason: str
) -> ChangeLedger | None:
    """A moved public URL gets a permanent redirect, after loop/chain validation."""
    return submit_change(
        kind="redirect_create",
        action=ChangeLedger.Action.CREATE,
        target_model="seo.redirect",
        target_label=f"{old_path} -> {new_path}",
        before={},
        after={
            "source": old_path,
            "target": new_path,
            "is_permanent": True,
            "is_active": True,
        },
        reason=reason,
        source=source,
        idempotency_key=f"redirect:{old_path}->{new_path}",
    )


def plan_from_issues() -> list[ChangeLedger]:
    """Turn measured ``redirect_chain`` issues into flatten changes."""
    planned: list[ChangeLedger] = []
    for issue in Issue.objects.filter(code="redirect_chain", status=Issue.Status.OPEN):
        source = str(issue.details.get("source", ""))
        redirect = Redirect.objects.filter(source=source, is_active=True).first()
        final = flattened_target(source) if redirect else None
        if redirect is None or final is None or final == redirect.target:
            continue
        change = submit_change(
            kind="redirect_flatten",
            action=ChangeLedger.Action.UPDATE,
            target_model="seo.redirect",
            target_pk=str(redirect.pk),
            target_label=f"{source} -> {final}",
            before={"target": redirect.target},
            after={"target": final},
            reason=f"Redirect chain: {source} now points straight to {final}.",
            source="audit:redirect_chain",
            idempotency_key=f"flatten:{source}:{redirect.target}->{final}",
        )
        if change is not None:
            planned.append(change)
    return planned
