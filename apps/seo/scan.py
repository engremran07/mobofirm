"""Full-site content scan: every public URL is resolved in-process and audited.

No HTTP is involved, so the scan runs in a dev server, a management command, a Celery
worker or a staff request alike. Each URL of the static routes and of every sitemap
section (pages, posts, brands, chipsets, models, firmware, tools, tags) goes through the
same resolver that builds the real page head; the result is checked against the
standards in ``rules`` and the object behind the page is checked for content quality.

Issues are upserted by fingerprint and closed when they stop reproducing. A scan is
bounded by a time budget: when it stops early the run is marked partial and the next run
continues from its cursor.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from django.db import DatabaseError
from django.urls import Resolver404, resolve
from django.utils import timezone

from . import audit
from .audit import Finding
from .contracts import ResolvedSEO
from .inventory import (
    describe,
    iter_static_routes,
    raw_sitemap_paths,
    resolve_path,
    sitemap_paths,
)
from .models import CrawlRun, Issue, PageSnapshot, SEOSettings
from .object_checks import Check, ContentFacts, content_facts
from .policy import is_private_path
from .proposals import DESCRIPTION_FIELD, GENERIC_SOURCES, TITLE_FIELD, propose
from .providers import get_provider
from .resolver import override_for
from .rules import (
    DESCRIPTION_MIN,
    SCAN_CODES,
    SLUG_MAX_LENGTH,
    TITLE_MAX,
    TITLE_MIN,
)
from .siteurls import site_origin

ERROR, WARNING, INFO = Issue.Severity.ERROR, Issue.Severity.WARNING, Issue.Severity.INFO
SYNC_BUDGET_SECONDS = 25.0
BACKGROUND_BUDGET_SECONDS = 600.0
RUNNING_STALE_AFTER = timedelta(hours=2)
SKIPPED_PAGE_TYPES = frozenset({"private", "unmapped"})
DUPLICATE_CODES = frozenset({"title_duplicate", "description_duplicate"})
CONTENT_DUPLICATE_CODE = "duplicate_content"
CONTENT_SCHEMA_TYPES = frozenset(
    {"blog_post", "model", "brand", "chipset", "firmware", "tool", "page"}
)
IMAGE_PAGE_TYPES = CONTENT_SCHEMA_TYPES
SCHEMA_REQUIRED: dict[str, tuple[str, ...]] = {
    "BlogPosting": ("headline",),
    "Article": ("headline",),
    "SoftwareApplication": ("name",),
    "CollectionPage": ("name", "url"),
    "WebPage": ("name", "url"),
    "BreadcrumbList": ("itemListElement",),
    "Organization": ("name", "url"),
    "WebSite": ("name", "url"),
}
THIN_REASON_PREFIXES = (
    "fewer than",
    "brand has no",
    "chipset has no",
    "category has no",
    "no active",
    "empty category",
    "duplicate of",
)
_BAD_SLUG = re.compile(r"[A-Z_]|--")


class ScanUnavailable(Exception):
    """A scan cannot start (disabled or already running)."""


@dataclass(frozen=True)
class Target:
    path: str
    section: str
    in_sitemap: bool


@dataclass
class ScanState:
    findings: list[Finding] = field(default_factory=list)
    scanned: set[str] = field(default_factory=set)
    digests: dict[str, list[tuple[str, dict[str, str]]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    errors: set[str] = field(default_factory=set)


def _is_page_route(path: str) -> bool:
    """A route the SEO resolver has a provider for (others are not public pages)."""
    try:
        match = resolve(urlsplit(path).path)
    except Resolver404:
        return False
    return get_provider(match.view_name) is not None


def collect_targets() -> list[Target]:
    """Every public URL: parameterless routes plus every sitemap section item."""
    listed = sitemap_paths()
    entries: dict[str, str] = {}
    for _name, path in iter_static_routes():
        if _is_page_route(path):
            entries.setdefault(path, "urlconf")
    for path, section in raw_sitemap_paths().items():
        entries[path] = f"sitemap:{section}"
    return [
        Target(path, section, path in listed)
        for path, section in sorted(entries.items())
        if not is_private_path(path)
    ]


def _finding(
    path: str,
    where: dict[str, str],
    code: str,
    severity: str,
    message: str,
    *,
    key: str = "",
    seo_field: str = "",
    measured: str = "",
    expected: str = "",
    suggestion: dict[str, str] | None = None,
    fixable: bool = False,
    **details: Any,
) -> Finding:
    return Finding(
        code,
        severity,
        path,
        message,
        key,
        details,
        seo_field=seo_field,
        measured=measured,
        expected=expected,
        suggestion=suggestion or {},
        fixable=fixable,
        group=where["group"],
        object_label=where["object_label"],
        object_pk=where["object_pk"],
        object_title=where["object_title"],
    )


def _slug_findings(path: str, where: dict[str, str]) -> list[Finding]:
    segments = [part for part in urlsplit(path).path.split("/") if part]
    problems: list[str] = []
    for segment in segments:
        if _BAD_SLUG.search(segment):
            problems.append(f"'{segment}' uses uppercase letters, underscores or --")
        elif len(segment) > SLUG_MAX_LENGTH:
            problems.append(f"'{segment[:30]}…' is {len(segment)} characters long")
    if not problems:
        return []
    return [
        _finding(
            path,
            where,
            "slug_quality",
            INFO,
            "; ".join(problems),
            measured=path,
            expected=f"lowercase words, single hyphens, under {SLUG_MAX_LENGTH} characters",
        )
    ]


def _schema_findings(
    resolved: ResolvedSEO, path: str, where: dict[str, str]
) -> list[Finding]:
    found: list[Finding] = []
    content_nodes = [
        node for node in resolved.schema if node.get("@type") != "BreadcrumbList"
    ]
    if resolved.page_type in CONTENT_SCHEMA_TYPES and not content_nodes:
        found.append(
            _finding(
                path,
                where,
                "schema_missing",
                INFO,
                "The page emits no structured data beyond breadcrumbs.",
            )
        )
    for node in resolved.schema:
        kind = str(node.get("@type", ""))
        missing = [name for name in SCHEMA_REQUIRED.get(kind, ()) if not node.get(name)]
        if not node.get("@context"):
            missing.append("@context")
        if not kind:
            missing.append("@type")
        if missing:
            found.append(
                _finding(
                    path,
                    where,
                    "schema_invalid",
                    WARNING,
                    f"{kind or 'A JSON-LD node'} lacks {', '.join(missing)}.",
                    key=f"{kind}:{','.join(missing)}",
                    measured=", ".join(missing),
                    expected="all required properties present",
                )
            )
    return found


def _metadata_findings(
    resolved: ResolvedSEO,
    path: str,
    where: dict[str, str],
    row: SEOSettings,
    site_name: str,
) -> list[Finding]:
    found: list[Finding] = []
    ref = resolved.object_ref

    def proposal(seo_field: str) -> dict[str, str]:
        if ref is None:
            return {}
        made = propose(seo_field, ref[0], ref[1], row, site_name)
        return made.as_dict() if made else {}

    title, title_source = resolved.title, resolved.provenance.get("title", "")
    if title_source == "fallback" or title.strip() == site_name.strip():
        suggestion = proposal(TITLE_FIELD)
        found.append(
            _finding(
                path,
                where,
                "title_missing",
                ERROR,
                "The page has no title of its own; it shows only the site name.",
                seo_field=TITLE_FIELD,
                measured=title,
                expected="a title that names this page",
                suggestion=suggestion,
                fixable=bool(suggestion),
            )
        )
    elif len(title) < TITLE_MIN:
        suggestion = proposal(TITLE_FIELD)
        found.append(
            _finding(
                path,
                where,
                "title_too_short",
                WARNING,
                f"The title is {len(title)} characters.",
                seo_field=TITLE_FIELD,
                measured=f"{len(title)} chars: {title}",
                expected=f"at least {TITLE_MIN} characters",
                suggestion=suggestion,
                fixable=bool(suggestion and suggestion["value"] != title),
            )
        )
    elif len(title) > TITLE_MAX:
        suggestion = proposal(TITLE_FIELD)
        found.append(
            _finding(
                path,
                where,
                "title_too_long",
                WARNING,
                f"The title is {len(title)} characters; search results cut it at "
                f"about {TITLE_MAX}.",
                seo_field=TITLE_FIELD,
                measured=f"{len(title)} chars: {title}",
                expected=f"at most {TITLE_MAX} characters",
                suggestion=suggestion,
                fixable=bool(suggestion and suggestion["value"] != title),
            )
        )

    description = resolved.description
    source = resolved.provenance.get("description", "")
    if source in GENERIC_SOURCES:
        suggestion = proposal(DESCRIPTION_FIELD)
        found.append(
            _finding(
                path,
                where,
                "description_missing",
                WARNING,
                "The page falls back to the site-wide description.",
                seo_field=DESCRIPTION_FIELD,
                measured=description,
                expected="a description written from this page's content",
                suggestion=suggestion,
                fixable=bool(suggestion),
            )
        )
    elif description.endswith("…"):
        suggestion = proposal(DESCRIPTION_FIELD)
        found.append(
            _finding(
                path,
                where,
                "description_truncated",
                INFO,
                "The description is cut off with an ellipsis.",
                seo_field=DESCRIPTION_FIELD,
                measured=description,
                expected="a complete description within the length limit",
                suggestion=suggestion,
                fixable=bool(suggestion and suggestion["value"] != description),
            )
        )
    elif len(description) < DESCRIPTION_MIN:
        suggestion = proposal(DESCRIPTION_FIELD)
        found.append(
            _finding(
                path,
                where,
                "description_too_short",
                INFO,
                f"The description is {len(description)} characters.",
                seo_field=DESCRIPTION_FIELD,
                measured=f"{len(description)} chars: {description}",
                expected=f"at least {DESCRIPTION_MIN} characters",
                suggestion=suggestion,
                fixable=bool(suggestion and suggestion["value"] != description),
            )
        )

    canonical_path = urlsplit(resolved.canonical).path if resolved.canonical else ""
    if (
        canonical_path
        and canonical_path != urlsplit(path).path
        and resolved.provenance.get("canonical") != "override"
    ):
        found.append(
            _finding(
                path,
                where,
                "canonical_not_self",
                WARNING,
                f"The canonical points to {canonical_path}.",
                measured=resolved.canonical,
                expected="the page's own URL",
            )
        )
    if resolved.page_type in IMAGE_PAGE_TYPES and "og:image" not in resolved.og:
        found.append(
            _finding(
                path,
                where,
                "social_image_missing",
                INFO,
                "No social preview image is available for this page.",
            )
        )
    return found


def _noindex_findings(
    resolved: ResolvedSEO, path: str, where: dict[str, str], in_raw_sitemap: bool
) -> list[Finding]:
    if not in_raw_sitemap:
        return []
    reason = resolved.noindex_reason
    if reason.startswith(THIN_REASON_PREFIXES):
        return [
            _finding(
                path,
                where,
                "thin_page",
                INFO,
                f"Kept out of the index: {reason}.",
                measured=reason,
            )
        ]
    ref = resolved.object_ref
    override = override_for(ref)
    removable = override is not None and override.noindex
    return [
        _finding(
            path,
            where,
            "noindex_public_content",
            WARNING,
            f"Public content is set to noindex ({reason or 'no reason recorded'}).",
            seo_field="noindex",
            measured="noindex",
            expected="indexable, unless deliberately excluded",
            suggestion={"field": "noindex", "value": "false", "basis": "override"}
            if removable
            else {},
            fixable=removable,
        )
    ]


def _checks_to_findings(
    checks: Iterable[Check],
    path: str,
    where: dict[str, str],
) -> list[Finding]:
    return [
        _finding(
            path,
            where,
            check.code,
            check.severity,
            check.message,
            key=check.key,
            measured=check.measured,
            expected=check.expected,
            **check.details,
        )
        for check in checks
    ]


def scan_target(
    target: Target,
    state: ScanState,
    row: SEOSettings,
    site_name: str,
    run: CrawlRun | None,
) -> ResolvedSEO | None:
    """Resolve and audit one URL; record its snapshot. ``None`` when not a page."""
    path = target.path
    resolved = resolve_path(path)
    if resolved is None:
        where = {
            "group": "site",
            "object_label": "",
            "object_pk": "",
            "object_title": "",
        }
        state.scanned.add(path)
        if target.section.startswith("sitemap:"):
            state.findings.append(
                _finding(
                    path,
                    where,
                    "sitemap_url_unresolvable",
                    ERROR,
                    "A sitemap URL does not resolve to any route.",
                )
            )
            state.errors.add(path)
        return None
    if resolved.page_type in SKIPPED_PAGE_TYPES:
        return None
    where = describe(resolved)
    state.scanned.add(path)
    found: list[Finding] = []
    facts = ContentFacts()
    ref = resolved.object_ref
    if ref is not None:
        obj = _load(ref)
        if obj is not None:
            facts = content_facts(ref[0], obj, indexable=resolved.indexable)
    if resolved.indexable:
        found.extend(_metadata_findings(resolved, path, where, row, site_name))
        found.extend(_schema_findings(resolved, path, where))
        if where["group"] != "site":
            found.extend(_slug_findings(path, where))
    else:
        found.extend(
            _noindex_findings(
                resolved, path, where, target.section.startswith("sitemap:")
            )
        )
    found.extend(_checks_to_findings(facts.checks, path, where))
    if facts.digest and resolved.indexable:
        state.digests[facts.digest].append((path, where))
    state.findings.extend(found)
    state.errors.update(f.url for f in found if f.severity == ERROR)
    PageSnapshot.objects.update_or_create(
        path=path[:500],
        defaults={
            "group": where["group"],
            "page_type": resolved.page_type,
            "object_label": where["object_label"],
            "object_pk": where["object_pk"],
            "object_title": where["object_title"],
            "title": resolved.title[:500],
            "title_source": resolved.provenance.get("title", "")[:16],
            "description": resolved.description[:500],
            "description_source": resolved.provenance.get("description", "")[:16],
            "canonical": resolved.canonical[:500],
            "indexable": resolved.indexable,
            "noindex_reason": resolved.noindex_reason[:255],
            "in_sitemap": target.in_sitemap,
            "word_count": facts.word_count,
            "last_run": run,
            "scanned_at": timezone.now(),
        },
    )
    return resolved


def _load(ref: tuple[str, str]) -> Any | None:
    from .proposals import load_object

    return load_object(ref[0], ref[1])


def _duplicate_findings() -> list[Finding]:
    """Titles and descriptions shared by several indexable pages (global state)."""
    found: list[Finding] = []
    snapshots = PageSnapshot.objects.filter(indexable=True)
    for code, attribute, label in (
        ("title_duplicate", "title", "Title"),
        ("description_duplicate", "description", "Description"),
    ):
        groups: dict[str, list[PageSnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            value = getattr(snapshot, attribute).strip()
            source = getattr(snapshot, f"{attribute}_source")
            if value and source not in GENERIC_SOURCES:
                groups[value].append(snapshot)
        for value, members in groups.items():
            if len(members) < 2:
                continue
            digest = hashlib.sha256(value.encode()).hexdigest()[:16]
            for snapshot in members:
                where = {
                    "group": snapshot.group,
                    "object_label": snapshot.object_label,
                    "object_pk": snapshot.object_pk,
                    "object_title": snapshot.object_title,
                }
                others = [m.path for m in members if m.pk != snapshot.pk][:5]
                found.append(
                    _finding(
                        snapshot.path,
                        where,
                        code,
                        WARNING,
                        f"{label} shared by {len(members)} pages.",
                        key=digest,
                        seo_field=TITLE_FIELD
                        if attribute == "title"
                        else DESCRIPTION_FIELD,
                        measured=value,
                        expected=f"a unique {attribute}",
                        pages=others,
                    )
                )
    return found


def _content_duplicates(state: ScanState) -> list[Finding]:
    found: list[Finding] = []
    for digest, members in state.digests.items():
        if len(members) < 2:
            continue
        for path, where in members:
            found.append(
                _finding(
                    path,
                    where,
                    CONTENT_DUPLICATE_CODE,
                    WARNING,
                    f"The opening text matches {len(members) - 1} other page(s).",
                    key=digest[:16],
                    expected="distinct opening content",
                    pages=[p for p, _w in members if p != path][:5],
                )
            )
    return found


def _resolve_stale(
    seen: set[str], scanned: set[str], codes: Iterable[str], *, all_urls: bool
) -> int:
    stale = Issue.objects.filter(status=Issue.Status.OPEN, code__in=list(codes))
    resolved = 0
    for issue in stale.exclude(fingerprint__in=seen):
        if all_urls or issue.url in scanned:
            audit.resolve_issue(issue)
            resolved += 1
    return resolved


def _site_name(row: SEOSettings) -> str:
    from apps.site_settings.services import get_site_settings

    return row.site_name or get_site_settings().site_name


def _guard(row: SEOSettings) -> None:
    if not row.seo_enabled or row.autonomy_mode == SEOSettings.AutonomyMode.OFF:
        raise ScanUnavailable("SEO automation is switched off.")
    cutoff = timezone.now() - RUNNING_STALE_AFTER
    if CrawlRun.objects.filter(
        kind=CrawlRun.Kind.SCAN, status=CrawlRun.Status.RUNNING, created_at__gte=cutoff
    ).exists():
        raise ScanUnavailable("A scan is already running.")


def run_scan(
    *,
    trigger: str = CrawlRun.Trigger.MANUAL,
    user: object | None = None,
    budget_seconds: float = SYNC_BUDGET_SECONDS,
    resume: bool = True,
    clock: Callable[[], float] = time.monotonic,
) -> CrawlRun:
    """Scan every public URL within the budget and sync the issues it found.

    ``resume`` continues after the cursor of the previous partial scan, so repeated
    bounded runs cover the whole site.
    """
    row = SEOSettings.get_solo()
    _guard(row)
    targets = collect_targets()
    start = 0
    if resume:
        last = CrawlRun.objects.filter(kind=CrawlRun.Kind.SCAN).first()
        if last is not None and last.partial and last.cursor < len(targets):
            start = last.cursor
    run = CrawlRun.objects.create(
        kind=CrawlRun.Kind.SCAN,
        base_url=site_origin() or "",
        trigger=trigger,
        urls_total=len(targets),
        cursor=start,
        started_at=timezone.now(),
        initiated_by_id=(
            getattr(user, "pk", None)
            if getattr(user, "is_authenticated", False)
            else None
        ),
    )
    state = ScanState()
    site_name = _site_name(row)
    deadline = clock() + budget_seconds
    index = start
    completed = False
    try:
        while index < len(targets):
            if index > start and clock() >= deadline:
                break
            scan_target(targets[index], state, row, site_name, run)
            index += 1
        completed = True
    except DatabaseError as error:
        run.error = str(error)[:2000]
        raise
    finally:
        if not completed:
            run.status = CrawlRun.Status.FAILED
            run.error = run.error or "Scan aborted unexpectedly."
            run.finished_at = timezone.now()
            run.save()
    return _finish(run, state, targets, index, start)


def _finish(
    run: CrawlRun, state: ScanState, targets: list[Target], index: int, start: int
) -> CrawlRun:
    complete = index >= len(targets)
    found = [*state.findings, *_content_duplicates(state), *_duplicate_findings()]
    before = Issue.objects.filter(status=Issue.Status.OPEN).count()
    seen = audit.upsert_findings(run, found)
    page_codes = SCAN_CODES - DUPLICATE_CODES - {CONTENT_DUPLICATE_CODE}
    resolved = _resolve_stale(seen, state.scanned, page_codes, all_urls=False)
    resolved += _resolve_stale(seen, state.scanned, DUPLICATE_CODES, all_urls=True)
    resolved += _resolve_stale(
        seen, state.scanned, {CONTENT_DUPLICATE_CODE}, all_urls=False
    )
    if complete:
        live = {target.path for target in targets}
        gone = Issue.objects.filter(
            status=Issue.Status.OPEN, code__in=list(SCAN_CODES)
        ).exclude(url__in=live)
        for issue in gone:
            audit.resolve_issue(issue)
            resolved += 1
    open_now = Issue.objects.filter(status=Issue.Status.OPEN)
    by_severity = {
        severity: open_now.filter(severity=severity).count()
        for severity in Issue.Severity.values
    }
    run.pages_crawled = len(state.scanned)
    run.pages_with_errors = len(state.errors)
    run.cursor = 0 if complete else index
    run.partial = not complete
    run.status = CrawlRun.Status.COMPLETED
    run.finished_at = timezone.now()
    run.summary = {
        "scanned": len(state.scanned),
        "total": len(targets),
        "started_at_offset": start,
        "findings": len(found),
        "resolved": resolved,
        "open_before": before,
        "open_after": open_now.count(),
        "open_by_severity": by_severity,
    }
    run.save()
    from . import fixes

    fixes.auto_apply(run)
    return run


def rescan_paths(paths: Iterable[str], *, duplicates: bool = True) -> dict[str, int]:
    """Re-audit specific URLs now (after a fix, rollback or content change).

    ``duplicates`` also refreshes the shared-title and shared-description findings,
    which read every snapshot; bulk callers such as the job queue skip it.
    """
    row = SEOSettings.get_solo()
    state = ScanState()
    site_name = _site_name(row)
    wanted = [
        path for path in dict.fromkeys(paths) if path and not is_private_path(path)
    ]
    known = dict(
        PageSnapshot.objects.filter(path__in=wanted).values_list("path", "in_sitemap")
    )
    listed: dict[str, str] = {}
    raw: dict[str, str] = {}
    if any(path not in known for path in wanted):
        listed, raw = sitemap_paths(), raw_sitemap_paths()
    for path in wanted:
        in_sitemap = known.get(path, path in listed)
        public = path in known or path in raw
        section = "sitemap:rescan" if public else "urlconf"
        scan_target(Target(path, section, in_sitemap), state, row, site_name, None)
    seen = audit.upsert_findings(None, state.findings)
    resolved = _resolve_stale(
        seen, state.scanned, SCAN_CODES - DUPLICATE_CODES, all_urls=False
    )
    if duplicates:
        dup = _duplicate_findings()
        audit.upsert_findings(None, dup)
        resolved += _resolve_stale(
            {f.fingerprint for f in dup}, state.scanned, DUPLICATE_CODES, all_urls=True
        )
    return {
        "scanned": len(state.scanned),
        "findings": len(state.findings),
        "resolved": resolved,
    }
