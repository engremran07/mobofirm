"""Issues board queries: filters, group counts and the links a reviewer needs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.db.models import Count, Q, QuerySet
from django.urls import NoReverseMatch, reverse

from .models import Issue
from .rules import (
    DESCRIPTION_MAX,
    DESCRIPTION_MIN,
    GROUP_LABELS,
    GROUPS,
    TITLE_MAX,
    TITLE_MIN,
    rule_for,
)

PAGE_SIZE = 25
STATUSES = (
    (Issue.Status.OPEN.value, "Open"),
    (Issue.Status.IGNORED.value, "Ignored"),
    (Issue.Status.RESOLVED.value, "Resolved"),
)


@dataclass(frozen=True)
class BoardFilter:
    status: str = Issue.Status.OPEN.value
    group: str = ""
    severity: str = ""
    code: str = ""
    query: str = ""
    fixable: bool = False

    def params(self, **overrides: Any) -> str:
        values = {
            "status": self.status,
            "group": self.group,
            "severity": self.severity,
            "code": self.code,
            "q": self.query,
            "fixable": "1" if self.fixable else "",
        }
        values.update({k: v for k, v in overrides.items()})
        return urlencode({k: v for k, v in values.items() if v})


def parse_filter(params: Any) -> BoardFilter:
    status = params.get("status", Issue.Status.OPEN.value)
    group = params.get("group", "")
    severity = params.get("severity", "")
    return BoardFilter(
        status=status if status in Issue.Status.values else Issue.Status.OPEN.value,
        group=group if group in GROUP_LABELS else "",
        severity=severity if severity in Issue.Severity.values else "",
        code=str(params.get("code", ""))[:64],
        query=str(params.get("q", "")).strip()[:100],
        fixable=params.get("fixable") == "1",
    )


def queryset(board: BoardFilter, *, with_group: bool = True) -> QuerySet[Issue]:
    issues = Issue.objects.filter(status=board.status)
    if with_group and board.group:
        issues = issues.filter(group=board.group)
    if board.severity:
        issues = issues.filter(severity=board.severity)
    if board.code:
        issues = issues.filter(code=board.code)
    if board.fixable:
        issues = issues.filter(fixable=True)
    if board.query:
        issues = issues.filter(
            Q(url__icontains=board.query)
            | Q(object_title__icontains=board.query)
            | Q(message__icontains=board.query)
        )
    return issues.order_by("severity", "group", "object_title", "code", "pk")


def group_tabs(board: BoardFilter) -> list[dict[str, Any]]:
    """One tab per review bucket with its issue count under the other filters."""
    counts = {
        row["group"]: row["total"]
        for row in queryset(board, with_group=False)
        .values("group")
        .annotate(total=Count("pk"))
    }
    tabs = [
        {
            "key": "",
            "label": "All",
            "total": sum(counts.values()),
            "current": not board.group,
            "query": board.params(group=""),
        }
    ]
    tabs.extend(
        {
            "key": key,
            "label": label,
            "total": counts.get(key, 0),
            "current": board.group == key,
            "query": board.params(group=key),
        }
        for key, label in GROUPS
        if counts.get(key) or board.group == key
    )
    return tabs


def admin_change_url(label: str, pk: str) -> str:
    app_label, _, model = label.partition(".")
    if not model:
        return ""
    try:
        return reverse(f"admin:{app_label}_{model}_change", args=[pk])
    except NoReverseMatch:
        return ""


FIELD_LIMITS: dict[str, tuple[str, int, int]] = {
    "meta_title": ("Title", TITLE_MIN, TITLE_MAX),
    "meta_description": ("Description", DESCRIPTION_MIN, DESCRIPTION_MAX),
    "noindex": ("Indexing", 0, 0),
}


def describe_issue(issue: Issue) -> dict[str, Any]:
    rule = rule_for(issue.code)
    label, minimum, maximum = FIELD_LIMITS.get(issue.seo_field, ("", 0, 0))
    return {
        "issue": issue,
        "rule": rule,
        "group_label": GROUP_LABELS.get(issue.group, ""),
        "admin_url": admin_change_url(issue.object_label, issue.object_pk),
        "proposal": (issue.suggestion or {}).get("value", ""),
        "basis": (issue.suggestion or {}).get("basis", ""),
        "field_label": label,
        "min_len": minimum,
        "max_len": maximum,
    }
