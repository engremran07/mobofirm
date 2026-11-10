"""Free-text search over the wallet history (same engine as every other list)."""

from __future__ import annotations

from django.db.models import Case, CharField, Expression, Value, When

from .models import LedgerReason

LEDGER_FIELDS = ("note", "reference", "event_key", "reason_label")


def _reason_label() -> Expression:
    """The human label of ``reason`` so "download" finds "Download" rows."""
    return Case(
        *[
            When(reason=code, then=Value(str(label)))
            for code, label in LedgerReason.choices
        ],
        default=Value(""),
        output_field=CharField(),
    )


LEDGER_ANNOTATIONS: dict[str, Expression] = {"reason_label": _reason_label()}
