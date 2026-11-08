"""Review-and-fix workflow: apply a standards-based proposal through the SEO override
layer with a before/after ledger entry, re-audit the object, and roll back on demand.

Fixes write ``seo.Metadata`` (the explicit override the resolver already prefers), so
no other app's tables are touched and every change can be undone from the ledger.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from . import autonomy, ledger
from .models import ChangeLedger, CrawlRun, Issue, Metadata, SEOModel, SEOSettings
from .proposals import DESCRIPTION_FIELD, TITLE_FIELD
from .rules import (
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    TITLE_MAX,
    TITLE_MIN,
    rule_for,
)

NOINDEX_FIELD = "noindex"
FIXABLE_FIELDS = frozenset({TITLE_FIELD, DESCRIPTION_FIELD, NOINDEX_FIELD})
ADD_CODES = frozenset({"title_missing", "description_missing"})
KIND_ADD, KIND_REWRITE = "metadata_fix_add", "metadata_fix_rewrite"


class FixError(Exception):
    """A fix cannot be applied; the message is shown to the reviewer."""


@dataclass
class BulkResult:
    applied: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)


def kind_for(issue: Issue) -> str:
    return KIND_ADD if issue.code in ADD_CODES else KIND_REWRITE


def can_edit(issue: Issue) -> bool:
    """Whether a reviewer can set this issue's field (with or without a proposal)."""
    return bool(
        issue.status == Issue.Status.OPEN
        and issue.seo_field in FIXABLE_FIELDS
        and issue.object_label
        and issue.object_pk
    )


def validate_value(seo_field: str, value: str) -> list[str]:
    """Reasons a value breaks the SEO standard for its field."""
    text = " ".join(value.split())
    if seo_field == TITLE_FIELD:
        if len(text) < TITLE_MIN:
            return [f"Title needs at least {TITLE_MIN} characters ({len(text)})."]
        if len(text) > TITLE_MAX:
            return [f"Title can have at most {TITLE_MAX} characters ({len(text)})."]
    elif seo_field == DESCRIPTION_FIELD:
        if len(text) < DESCRIPTION_MIN:
            return [
                f"Description needs at least {DESCRIPTION_MIN} characters ({len(text)})."
            ]
        if len(text) > DESCRIPTION_MAX:
            return [
                f"Description is {len(text)} characters; the limit is {DESCRIPTION_MAX}."
            ]
    elif seo_field == NOINDEX_FIELD:
        if value.strip().lower() not in {"true", "false"}:
            return ["Use true or false."]
    else:
        return [f"{seo_field or 'This field'} cannot be edited here."]
    return []


def _container(label: str, pk: str) -> SEOModel:
    app_label, _, model = label.partition(".")
    try:
        content_type = ContentType.objects.get(app_label=app_label, model=model)
    except ContentType.DoesNotExist:
        raise FixError(f"Unknown content type {label}.") from None
    container, _created = SEOModel.objects.get_or_create(
        content_type=content_type, object_id=str(pk)
    )
    if container.is_deleted:
        container.is_deleted = False
        container.save()
    return container


def _typed(seo_field: str, value: str) -> Any:
    return value.strip().lower() == "true" if seo_field == NOINDEX_FIELD else value


def _check_mode() -> SEOSettings:
    row = SEOSettings.get_solo()
    if not row.seo_enabled or row.autonomy_mode == SEOSettings.AutonomyMode.OFF:
        raise FixError("SEO automation is switched off.")
    return row


def _path_of(issue: Issue) -> str:
    parts = urlsplit(issue.url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def proposed_value(issue: Issue) -> str:
    return str((issue.suggestion or {}).get("value", ""))


def apply_fix(
    issue: Issue,
    user: Any,
    *,
    value: str | None = None,
    source: str = "",
    reaudit: bool = True,
) -> ChangeLedger:
    """Write ``value`` (or the stored proposal) to the object's SEO override."""
    row = _check_mode()
    issue.refresh_from_db()
    if not can_edit(issue):
        raise FixError("This issue cannot be fixed from the SEO screens.")
    final = " ".join((value if value is not None else proposed_value(issue)).split())
    if not final:
        raise FixError("There is no proposal; enter a value.")
    problems = validate_value(issue.seo_field, final)
    if problems:
        raise FixError(" ".join(problems))
    typed = _typed(issue.seo_field, final)
    with transaction.atomic():
        container = _container(issue.object_label, issue.object_pk)
        metadata = Metadata.objects.filter(seo=container).first()
        if metadata is not None:
            current = getattr(metadata, issue.seo_field)
            if current == typed:
                raise FixError("The override already holds this value.")
            action = ChangeLedger.Action.UPDATE
            target_pk = str(metadata.pk)
            before = {issue.seo_field: current}
            after = {issue.seo_field: typed}
        else:
            action = ChangeLedger.Action.CREATE
            target_pk = ""
            before = {}
            after = {"seo_id": container.pk, issue.seo_field: typed}
        digest = hashlib.sha256(final.encode()).hexdigest()[:12]
        attempts = ChangeLedger.objects.filter(
            idempotency_key__startswith=f"fix:{issue.pk}:{digest}:"
        ).count()
        entry = ChangeLedger.objects.create(
            idempotency_key=f"fix:{issue.pk}:{digest}:{attempts}",
            kind=kind_for(issue),
            action=action,
            target_model="seo.metadata",
            target_pk=target_pk,
            target_label=f"{issue.object_title or issue.url}: {issue.seo_field}"[:255],
            before=before,
            after=after,
            reason=rule_for(issue.code).label[:500],
            source=source or f"review:{issue.code}",
            mode=row.autonomy_mode,
            requires_approval=False,
            decided_by=user if getattr(user, "is_authenticated", False) else None,
            decided_at=timezone.now(),
            validation={"errors": []},
        )
        entry = ledger.apply_change(entry, user)
        if entry.status != ChangeLedger.Status.APPLIED:
            raise FixError(entry.error or "The change could not be applied.")
        Issue.objects.filter(pk=issue.pk).update(fix_ledger=entry)
    if reaudit:
        reaudit_issue(issue)
    return entry


def reaudit_issue(issue: Issue) -> dict[str, int]:
    """Re-audit just this issue's URL so a fixed issue closes immediately."""
    from . import scan

    paths = [_path_of(issue)] if issue.url else []
    return scan.rescan_paths(paths)


def ignore_issue(issue: Issue, user: Any, reason: str) -> Issue:
    text = " ".join(reason.split())
    if len(text) < 3:
        raise FixError("Say why this issue is ignored (at least 3 characters).")
    if issue.status != Issue.Status.OPEN:
        raise FixError("Only open issues can be ignored.")
    issue.status = Issue.Status.IGNORED
    issue.ignored_reason = text[:255]
    issue.ignored_by = user if getattr(user, "is_authenticated", False) else None
    issue.ignored_at = timezone.now()
    issue.save()
    return issue


def restore_issue(issue: Issue) -> Issue:
    if issue.status != Issue.Status.IGNORED:
        raise FixError("Only ignored issues can be restored.")
    issue.status = Issue.Status.OPEN
    issue.ignored_reason = ""
    issue.ignored_by = None
    issue.ignored_at = None
    issue.save()
    return issue


def safe_candidates(queryset: Any) -> list[Issue]:
    """Open issues whose proposal only fills a missing field (low risk)."""
    return [
        issue
        for issue in queryset.filter(
            status=Issue.Status.OPEN, fixable=True, code__in=ADD_CODES
        )
        if can_edit(issue) and proposed_value(issue)
    ]


def apply_many(issues: Iterable[Issue], user: Any, *, source: str) -> BulkResult:
    result = BulkResult()
    paths: list[str] = []
    for issue in issues:
        try:
            apply_fix(issue, user, source=source, reaudit=False)
        except FixError as error:
            result.failed.append(f"{issue.url}: {error}")
            continue
        result.applied += 1
        paths.append(_path_of(issue))
    if paths:
        from . import scan

        scan.rescan_paths(paths)
    result.skipped = len(result.failed)
    return result


def auto_apply(run: CrawlRun) -> BulkResult:
    """After a scan, apply what the autonomy mode permits; suggest mode applies none."""
    row = SEOSettings.get_solo()
    mode = row.autonomy_mode
    if not row.seo_enabled:
        return BulkResult()
    candidates: list[Issue] = []
    queryset = Issue.objects.filter(status=Issue.Status.OPEN, fixable=True)
    for issue in queryset:
        if not can_edit(issue) or not proposed_value(issue):
            continue
        if autonomy.decide(kind_for(issue), mode).apply:
            candidates.append(issue)
    if not candidates:
        return BulkResult()
    return apply_many(candidates, None, source=f"autopilot:scan-{run.pk}")


def after_rollback(entry: ChangeLedger) -> None:
    """Re-audit the objects whose fix was just undone so the issue reopens."""
    issues = list(Issue.objects.filter(fix_ledger=entry))
    paths = [_path_of(issue) for issue in issues if issue.url]
    if paths:
        from . import scan

        scan.rescan_paths(paths)
