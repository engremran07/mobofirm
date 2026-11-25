"""Everything the "View and fix" screen shows for one page, built from real state.

The screen is addressed by URL path. Its owner is the content object behind the page,
or the route itself when the page has none, so every scanned URL is fixable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from django.apps import apps as django_apps
from django.core.exceptions import FieldDoesNotExist
from django.db.models import Q
from django.urls import reverse

from . import analysis, editable, editing, fixes
from .board import admin_change_url
from .fixplan import Plan, plan_for
from .inventory import locate, resolve_path
from .models import Issue
from .policy import is_private_path, is_route_ref, route_ref
from .proposals import DESCRIPTION_FIELD, TITLE_FIELD
from .rules import GROUP_LABELS, rule_for
from .siteurls import site_origin

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class FieldView:
    kind: str  # override | object
    name: str
    label: str
    input: str  # text | textarea | url | bool
    value: str
    original: str
    help_text: str = ""
    max_length: int = 0
    findings: list[str] = field(default_factory=list)
    flagged: bool = False
    error: str = ""

    @property
    def ident(self) -> str:
        return f"fix-{self.name}"

    @property
    def post_name(self) -> str:
        return f"f_{self.name}"

    @property
    def original_name(self) -> str:
        return f"o_{self.name}"


@dataclass
class AltInput:
    position: int
    src: str
    value: str = ""


@dataclass
class ConsoleLink:
    label: str
    url: str


@dataclass
class Screen:
    path: str
    label: str
    pk: str
    title: str
    group_label: str
    is_route: bool
    issues: list[Issue]
    focus: Issue | None
    flagged: list[FieldView]
    other: list[FieldView]
    alt_inputs: list[AltInput]
    alt_flagged: bool
    h1_in_body: int
    h1_flagged: bool
    console_links: list[ConsoleLink]
    guides_url: str
    admin_url: str
    page: analysis.PageAnalysis
    serp: analysis.SerpPreview
    resolved_title: str
    resolved_robots: str
    resolved_canonical: str
    indexable: bool
    noindex_reason: str
    labels: dict[int, str] = field(default_factory=dict)

    @property
    def fields(self) -> list[FieldView]:
        return [*self.flagged, *self.other]


def owner_of(path: str) -> tuple[str, str, str, str] | None:
    """(label, pk, title, group) of the object or route behind ``path``."""
    clean = urlsplit(path).path or "/"
    if is_private_path(clean):
        return None
    issue = (
        Issue.objects.filter(url=path).exclude(object_label="").order_by("pk").first()
    )
    if issue is not None:
        return issue.object_label, issue.object_pk, issue.object_title, issue.group
    where = locate(path)
    if where["object_label"]:
        return (
            where["object_label"],
            where["object_pk"],
            where["object_title"],
            where["group"],
        )
    if resolve_path(path) is None:
        return None
    label, pk = route_ref(clean)
    return label, pk, clean, "site"


def issues_of(path: str, label: str, pk: str) -> list[Issue]:
    scope = Q(url=path)
    if label and pk and not is_route_ref(label, pk):
        scope |= Q(object_label=label, object_pk=pk)
    rows = Issue.objects.filter(scope, status=Issue.Status.OPEN)
    return sorted(
        rows,
        key=lambda row: (SEVERITY_ORDER.get(row.severity, 3), row.code, row.pk),
    )


def next_issue(after_path: str) -> Issue | None:
    """The next open issue (board order) that is not on ``after_path``."""
    rows = Issue.objects.filter(status=Issue.Status.OPEN).exclude(url=after_path)
    ordered = sorted(
        rows,
        key=lambda row: (
            SEVERITY_ORDER.get(row.severity, 3),
            row.group,
            row.object_title,
            row.code,
            row.pk,
        ),
    )
    return ordered[0] if ordered else None


def _model_has(label: str, name: str) -> bool:
    try:
        django_apps.get_model(label)._meta.get_field(name)
    except (LookupError, ValueError, FieldDoesNotExist):
        return False
    return True


def _merge_plans(open_issues: list[Issue]) -> tuple[Plan, dict[str, list[str]]]:
    overrides: list[str] = []
    objects: list[str] = []
    console: list[str] = []
    messages: dict[str, list[str]] = {}
    alts = h1 = guides = False
    for issue in open_issues:
        plan = plan_for(issue.code)
        text = f"{rule_for(issue.code).label}: {issue.message}"
        for name in (*plan.overrides, *plan.objects):
            messages.setdefault(name, []).append(text)
        overrides.extend(plan.overrides)
        objects.extend(plan.objects)
        console.extend(plan.console)
        alts = alts or plan.alts
        h1 = h1 or plan.h1
        guides = guides or plan.guides
    merged = Plan(
        tuple(dict.fromkeys(overrides)),
        tuple(dict.fromkeys(objects)),
        alts,
        h1,
        tuple(dict.fromkeys(console)),
        guides,
    )
    return merged, messages


def _suggestion(open_issues: list[Issue], name: str) -> str:
    for issue in open_issues:
        suggestion = issue.suggestion or {}
        if suggestion.get("field") == name and suggestion.get("value"):
            return str(suggestion["value"])
    return ""


def _override_views(
    stored: dict[str, Any],
    resolved_title: str,
    resolved_description: str,
    open_issues: list[Issue],
    plan: Plan,
    messages: dict[str, list[str]],
    draft: dict[str, str],
) -> list[FieldView]:
    views: list[FieldView] = []
    for name, spec in fixes.OVERRIDE_SPECS.items():
        raw = stored.get(name)
        if name in fixes.BOOL_FIELDS:
            original = "true" if raw else "false"
            input_kind = "bool"
        else:
            original = str(raw or "")
            if not original and name == TITLE_FIELD:
                original = _suggestion(open_issues, name) or resolved_title
            elif not original and name == DESCRIPTION_FIELD:
                original = _suggestion(open_issues, name) or resolved_description
            elif not original:
                original = _suggestion(open_issues, name)
            input_kind = spec.kind if spec.kind in {"textarea", "url"} else "text"
        views.append(
            FieldView(
                "override",
                name,
                spec.label,
                input_kind,
                draft.get(f"f_{name}", original),
                original,
                spec.help_text,
                spec.max_length,
                messages.get(name, []),
                name in plan.overrides,
            )
        )
    return views


def build(
    path: str,
    *,
    focus_pk: int | None = None,
    draft: dict[str, str] | None = None,
    errors: dict[str, str] | None = None,
) -> Screen | None:
    owner = owner_of(path)
    if owner is None:
        return None
    label, pk, title, group = owner
    draft = draft or {}
    open_issues = issues_of(path, label, pk)
    focus = next((row for row in open_issues if row.pk == focus_pk), None) or (
        open_issues[0] if open_issues else None
    )
    plan, messages = _merge_plans(open_issues)
    resolved = resolve_path(path)
    page = analysis.analyse(path)
    resolved_title = resolved.title if resolved else page.title
    resolved_description = resolved.description if resolved else page.description
    views = _override_views(
        editing.override_values(label, pk),
        resolved_title,
        resolved_description,
        open_issues,
        plan,
        messages,
        draft,
    )
    obj = None if is_route_ref(label, pk) else editable.load(label, pk)
    if obj is not None:
        for spec in editable.specs_for(label):
            current = editable.read(obj, spec.name)
            views.append(
                FieldView(
                    "object",
                    spec.name,
                    spec.label,
                    "textarea" if spec.kind == "textarea" else "text",
                    draft.get(f"f_{spec.name}", current),
                    current,
                    spec.help_text,
                    spec.max_length,
                    messages.get(spec.name, []),
                    spec.name in plan.objects,
                )
            )
    for view in views:
        view.error = (errors or {}).get(view.name, "")
        if view.error:
            view.flagged = True
    alt_inputs: list[AltInput] = []
    h1_in_body = 0
    html_name = editable.html_field_for(label) if obj is not None else ""
    if obj is not None and html_name:
        markup = editable.read(obj, html_name)
        missing = [image for image in editable.body_images(markup) if image.alt is None]
        alt_inputs = [
            AltInput(position, image.src, draft.get(f"alt_{position}", ""))
            for position, image in enumerate(missing)
        ]
        h1_in_body = editable.body_h1_count(markup)
    admin_url = "" if is_route_ref(label, pk) else admin_change_url(label, pk)
    links = [
        ConsoleLink(f"Edit {name.replace('_', ' ')}", f"{admin_url}#id_{name}")
        for name in plan.console
        if admin_url and _model_has(label, name)
    ]
    if admin_url and not links:
        links.append(ConsoleLink("Edit in the console", admin_url))
    return Screen(
        path=path,
        label=label,
        pk=pk,
        title=title or path,
        group_label=GROUP_LABELS.get(group, ""),
        is_route=is_route_ref(label, pk),
        issues=open_issues,
        focus=focus,
        flagged=[view for view in views if view.flagged],
        other=[view for view in views if not view.flagged],
        alt_inputs=alt_inputs,
        alt_flagged=plan.alts,
        h1_in_body=h1_in_body,
        h1_flagged=plan.h1,
        console_links=links,
        guides_url=reverse("seo:guides") if plan.guides else "",
        admin_url=admin_url,
        page=page,
        serp=analysis.serp_preview(
            draft.get("f_meta_title") or resolved_title,
            draft.get("f_meta_description") or resolved_description,
            f"{site_origin()}{path}",
        ),
        resolved_title=resolved_title,
        resolved_robots=resolved.robots if resolved else "",
        resolved_canonical=resolved.canonical if resolved else "",
        indexable=bool(resolved and resolved.indexable),
        noindex_reason=resolved.noindex_reason if resolved else "",
        labels={row.pk: rule_for(row.code).label for row in open_issues},
    )


def _changed(post: Any, name: str) -> str | None:
    """The posted value when it differs from the value shown, else ``None``."""
    value = post.get(f"f_{name}")
    if value is None:
        return None
    shown = post.get(f"o_{name}", "")
    posted = value.replace("\r\n", "\n")
    return None if posted.strip() == shown.replace("\r\n", "\n").strip() else posted


def parse_submission(post: Any, label: str) -> editing.ChangeSet:
    """The edits in a POST: only fields whose value differs from what was shown."""
    changes = editing.ChangeSet()
    object_names = {spec.name for spec in editable.specs_for(label)}
    for name in fixes.OVERRIDE_SPECS:
        value = _changed(post, name)
        if value is not None:
            changes.overrides[name] = value
    for name in object_names:
        value = _changed(post, name)
        if value is not None:
            changes.object_fields[name] = value
    for key, value in post.items():
        if (
            key.startswith("alt_")
            and key[4:].isdigit()
            and isinstance(value, str)
            and value.strip()
        ):
            position = int(key[4:])
            changes.alts[position] = value
            source = post.get(f"altsrc_{position}")
            if source is not None:
                changes.alt_sources[position] = source
    changes.demote_h1 = post.get("demote_h1") == "1"
    return changes
