"""Staff SEO Control Center: thin views over the services in this app.

Every screen renders inside the admin shell (``render_admin_page``). State-changing
endpoints are POST-only, CSRF protected, permission checked and redirect afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from apps.core.admin_pages import render_admin_page

from . import board, fixes, interlinking, jobs, ledger, scan
from . import guides as guides_service
from .crawler import CrawlUnavailable, run_crawl
from .forms import SEOSettingsForm
from .integrations.status import integration_statuses
from .inventory import build_inventory, inspect_url
from .models import (
    ChangeLedger,
    CrawlRun,
    Issue,
    LinkSuggestion,
    PageSnapshot,
    SEOJob,
    SEOSettings,
)
from .rules import GROUP_LABELS, GROUPS, rule_for
from .sitemap_registry import get_sitemaps

staff_only: Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]] = (
    staff_member_required(login_url="admin:login")
)

_PAST = {"accept": "accepted", "reject": "rejected", "reset": "reopened"}

TABS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "seo:dashboard", "Overview"),
    ("issues", "seo:issues", "Issues board"),
    ("links", "seo:links", "Interlinking"),
    ("guides", "seo:guides", "Flash guides"),
    ("inventory", "seo:inventory", "Page inventory"),
    ("inspect", "seo:inspect", "Page inspector"),
    ("ledger", "seo:ledger", "Change ledger"),
    ("jobs", "seo:jobs", "Jobs"),
    ("settings", "seo:settings", "Settings"),
)


def _tabs(current: str) -> list[dict[str, Any]]:
    return [
        {"key": key, "url": reverse(name), "label": label, "current": key == current}
        for key, name, label in TABS
    ]


def _page(
    request: HttpRequest,
    template: str,
    *,
    title: str,
    tab: str,
    subtitle: str = "",
    context: dict[str, Any] | None = None,
) -> HttpResponse:
    return render_admin_page(
        request,
        template,
        title=title,
        subtitle=subtitle,
        context={"seo_tabs": _tabs(tab), "seo_tab": tab, **(context or {})},
    )


def _require(request: HttpRequest, *permissions: str) -> None:
    if not all(request.user.has_perm(permission) for permission in permissions):
        raise PermissionDenied


def _next_url(request: HttpRequest, fallback: str) -> str:
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


@require_GET
@staff_only
def dashboard(request: HttpRequest) -> HttpResponse:
    sections = [
        {"name": name, "urls": sitemap_class().paginator.count}
        for name, sitemap_class in get_sitemaps().items()
    ]
    latest = CrawlRun.objects.filter(status=CrawlRun.Status.COMPLETED).first()
    latest_scan = CrawlRun.objects.filter(
        kind=CrawlRun.Kind.SCAN, status=CrawlRun.Status.COMPLETED
    ).first()
    open_issues = Issue.objects.filter(status=Issue.Status.OPEN)
    by_severity = dict(open_issues.values_list("severity").annotate(total=Count("pk")))
    by_group = dict(open_issues.values_list("group").annotate(total=Count("pk")))
    week = timezone.now() - timedelta(days=7)
    fixes_applied = ChangeLedger.objects.filter(
        kind__startswith="metadata_fix", status=ChangeLedger.Status.APPLIED
    )
    snapshots = PageSnapshot.objects.all()
    return _page(
        request,
        "seo/dashboard.html",
        title="SEO Control Center",
        tab="dashboard",
        subtitle="Scan every public page, review what is wrong and fix it to standard.",
        context={
            "seo_settings": SEOSettings.get_solo(),
            "sections": sections,
            "latest_run": latest,
            "latest_scan": latest_scan,
            "recent_runs": CrawlRun.objects.all()[:6],
            "open_errors": by_severity.get(Issue.Severity.ERROR, 0),
            "open_warnings": by_severity.get(Issue.Severity.WARNING, 0),
            "open_info": by_severity.get(Issue.Severity.INFO, 0),
            "open_total": open_issues.count(),
            "group_rows": [
                {
                    "key": key,
                    "label": label,
                    "total": by_group.get(key, 0),
                    "url": f"{reverse('seo:issues')}?group={key}",
                }
                for key, label in GROUPS
            ],
            "fixes_total": fixes_applied.count(),
            "fixes_week": fixes_applied.filter(applied_at__gte=week).count(),
            "snapshot_total": snapshots.count(),
            "link_counts": interlinking.status_counts(),
            "snapshot_indexable": snapshots.filter(indexable=True).count(),
            "pending_changes": ChangeLedger.objects.filter(
                status=ChangeLedger.Status.PROPOSED
            ).count(),
            "failed_jobs": SEOJob.objects.filter(status=SEOJob.Status.FAILED).count(),
            "pending_jobs": SEOJob.objects.filter(status=SEOJob.Status.PENDING).count(),
            "integrations": integration_statuses(),
            "can_crawl": request.user.has_perm("seo.run_crawl"),
            "can_process": request.user.has_perm("seo.change_seojob"),
        },
    )


@require_POST
@staff_only
def run_scan(request: HttpRequest) -> HttpResponse:
    """Scan every public URL now, in this request, within a bounded time budget."""
    _require(request, "seo.run_crawl")
    restart = request.POST.get("restart") == "1"
    try:
        run = scan.run_scan(user=request.user, resume=not restart)
    except scan.ScanUnavailable as reason:
        messages.error(request, str(reason))
    else:
        summary = run.summary
        text = (
            f"Scan {run.pk}: {run.pages_crawled} of {run.urls_total} URLs checked, "
            f"{summary.get('open_after', 0)} open issues, "
            f"{summary.get('resolved', 0)} resolved."
        )
        if run.partial:
            messages.warning(
                request, f"{text} Time budget reached; run again to continue."
            )
        else:
            messages.success(request, text)
    return redirect(_next_url(request, reverse("seo:dashboard")))


@require_GET
@staff_only
def inventory(request: HttpRequest) -> HttpResponse:
    group = request.GET.get("group", "")
    index = request.GET.get("index", "")
    query = request.GET.get("q", "").strip()[:100]
    snapshots = PageSnapshot.objects.all()
    if group in GROUP_LABELS:
        snapshots = snapshots.filter(group=group)
    if index in {"yes", "no"}:
        snapshots = snapshots.filter(indexable=index == "yes")
    if query:
        snapshots = snapshots.filter(
            Q(path__icontains=query)
            | Q(title__icontains=query)
            | Q(object_title__icontains=query)
        )
    page = Paginator(snapshots, 50).get_page(request.GET.get("page"))
    issue_counts = dict(
        Issue.objects.filter(
            status=Issue.Status.OPEN, url__in=[row.path for row in page.object_list]
        )
        .values_list("url")
        .annotate(total=Count("pk"))
    )
    rows = [
        {"snapshot": row, "open_issues": issue_counts.get(row.path, 0)}
        for row in page.object_list
    ]
    items = build_inventory()
    filters = urlencode(
        {k: v for k, v in (("group", group), ("index", index), ("q", query)) if v}
    )
    return _page(
        request,
        "seo/inventory.html",
        title="Page inventory",
        tab="inventory",
        subtitle="Every public URL found by the last scan, with the head it serves.",
        context={
            "page": page,
            "rows": rows,
            "group": group,
            "index": index,
            "q": query,
            "filters": filters,
            "groups": GROUPS,
            "items": items,
            "indexable_total": sum(1 for item in items if item.indexable),
            "conflict_total": sum(1 for item in items if item.conflicts),
            "scanned_total": PageSnapshot.objects.count(),
            "can_scan": request.user.has_perm("seo.run_crawl"),
        },
    )


@require_GET
@staff_only
def inspect(request: HttpRequest) -> HttpResponse:
    url = request.GET.get("url", "").strip()
    report = inspect_url(url) if url else None
    snapshot = None
    if report is not None:
        snapshot = PageSnapshot.objects.filter(path=str(report["path"])).first()
    return _page(
        request,
        "seo/inspect.html",
        title="Page inspector",
        tab="inspect",
        context={"url": url, "report": report, "snapshot": snapshot},
    )


def _board_context(request: HttpRequest) -> dict[str, Any]:
    flt = board.parse_filter(request.GET)
    scoped = board.queryset(flt)
    page = Paginator(scoped, board.PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [board.describe_issue(issue) for issue in page.object_list]
    codes = (
        Issue.objects.filter(status=flt.status)
        .order_by("code")
        .values_list("code", flat=True)
        .distinct()
    )
    return {
        "flt": flt,
        "page": page,
        "rows": rows,
        "tabs": board.group_tabs(flt),
        "codes": [(code, rule_for(code).label) for code in codes],
        "statuses": board.STATUSES,
        "query_string": flt.params(),
        "safe_total": len(fixes.safe_candidates(board.queryset(flt))),
        "can_fix": request.user.has_perm("seo.change_metadata"),
        "can_manage": request.user.has_perm("seo.change_issue"),
        "can_scan": request.user.has_perm("seo.run_crawl"),
        "latest_scan": CrawlRun.objects.filter(
            kind=CrawlRun.Kind.SCAN, status=CrawlRun.Status.COMPLETED
        ).first(),
        "next_url": request.get_full_path(),
    }


@require_GET
@staff_only
def issues(request: HttpRequest) -> HttpResponse:
    context = _board_context(request)
    if request.headers.get("HX-Request"):
        return render(request, "seo/partials/issue_rows.html", context)
    return _page(
        request,
        "seo/issues.html",
        title="SEO issues board",
        tab="issues",
        subtitle="Review every finding by object type and correct it to SEO standard.",
        context=context,
    )


def _issue_context(request: HttpRequest, issue: Issue) -> dict[str, Any]:
    described = board.describe_issue(issue)
    return {
        **described,
        "can_fix": request.user.has_perm("seo.change_metadata")
        and fixes.can_edit(issue),
        "can_manage": request.user.has_perm("seo.change_issue"),
        "next_url": request.GET.get("next") or reverse("seo:issues"),
        "fix_ledger": issue.fix_ledger,
    }


@require_GET
@staff_only
def issue_detail(request: HttpRequest, pk: int) -> HttpResponse:
    issue = get_object_or_404(Issue, pk=pk)
    context = _issue_context(request, issue)
    if request.headers.get("HX-Request"):
        return render(request, "seo/partials/fix_panel.html", context)
    return _page(
        request,
        "seo/issue_detail.html",
        title=rule_for(issue.code).label,
        tab="issues",
        subtitle=issue.object_title or issue.url,
        context=context,
    )


@require_POST
@staff_only
def issue_apply(request: HttpRequest, pk: int) -> HttpResponse:
    _require(request, "seo.change_metadata")
    issue = get_object_or_404(Issue, pk=pk)
    back = _next_url(request, reverse("seo:issues"))
    try:
        entry = fixes.apply_fix(issue, request.user, value=request.POST.get("value"))
    except fixes.FixError as error:
        messages.error(request, str(error))
        detail = reverse("seo:issue_detail", args=[issue.pk])
        return redirect(f"{detail}?{urlencode({'next': back})}")
    issue.refresh_from_db()
    if issue.status == Issue.Status.OPEN:
        messages.warning(
            request,
            f"Change {entry.pk} was applied but the issue still reproduces; "
            "review the value.",
        )
    else:
        messages.success(
            request,
            f"Fixed: {rule_for(issue.code).label}. Change {entry.pk} can be rolled "
            "back from the ledger.",
        )
    return redirect(back)


@require_POST
@staff_only
def issue_ignore(request: HttpRequest, pk: int) -> HttpResponse:
    _require(request, "seo.change_issue")
    issue = get_object_or_404(Issue, pk=pk)
    try:
        fixes.ignore_issue(issue, request.user, request.POST.get("reason", ""))
    except fixes.FixError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Issue ignored. It is kept with your reason.")
    return redirect(_next_url(request, reverse("seo:issues")))


@require_POST
@staff_only
def issue_restore(request: HttpRequest, pk: int) -> HttpResponse:
    _require(request, "seo.change_issue")
    issue = get_object_or_404(Issue, pk=pk)
    try:
        fixes.restore_issue(issue)
    except fixes.FixError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, "Issue reopened.")
    return redirect(_next_url(request, reverse("seo:issues")))


@require_POST
@staff_only
def issue_reaudit(request: HttpRequest, pk: int) -> HttpResponse:
    _require(request, "seo.change_issue")
    issue = get_object_or_404(Issue, pk=pk)
    outcome = fixes.reaudit_issue(issue)
    issue.refresh_from_db()
    if issue.status == Issue.Status.RESOLVED:
        messages.success(request, "Re-audited: the issue no longer reproduces.")
    else:
        messages.info(
            request, f"Re-audited {outcome['scanned']} page(s): the issue still stands."
        )
    return redirect(_next_url(request, reverse("seo:issues")))


@staff_only
def issues_bulk(request: HttpRequest) -> HttpResponse:
    """Preview, then apply, every safe suggestion in the current filter."""
    _require(request, "seo.change_metadata")
    source = request.POST if request.method == "POST" else request.GET
    flt = board.parse_filter(source)
    candidates = fixes.safe_candidates(board.queryset(flt))
    if request.method == "POST":
        result = fixes.apply_many(candidates, request.user, source="review:bulk")
        if result.applied:
            messages.success(
                request,
                f"Applied {result.applied} safe suggestion(s). Each one is in the "
                "ledger and can be rolled back.",
            )
        for failure in result.failed[:5]:
            messages.error(request, failure)
        if not result.applied and not result.failed:
            messages.info(request, "There was nothing safe to apply.")
        return redirect(f"{reverse('seo:issues')}?{flt.params()}")
    preview = [
        {
            "issue": issue,
            "rule": rule_for(issue.code),
            "before": issue.measured,
            "after": fixes.proposed_value(issue),
        }
        for issue in candidates[:50]
    ]
    return _page(
        request,
        "seo/bulk_preview.html",
        title="Apply all safe suggestions",
        tab="issues",
        subtitle="Only fills missing titles and descriptions from each object's own data.",
        context={
            "flt": flt,
            "query_string": flt.params(),
            "total": len(candidates),
            "preview": preview,
        },
    )


@require_GET
@staff_only
def links(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", LinkSuggestion.Status.PROPOSED.value)
    if status not in LinkSuggestion.Status.values:
        status = LinkSuggestion.Status.PROPOSED.value
    relation = request.GET.get("relation", "")
    query = request.GET.get("q", "").strip()[:100]
    suggestions = (
        LinkSuggestion.objects.exclude(relation="")
        .filter(status=status)
        .select_related("source", "target")
        .order_by("source__title", "-score", "target__title")
    )
    if relation in interlinking.RELATION_LABELS:
        suggestions = suggestions.filter(relation=relation)
    if query:
        suggestions = suggestions.filter(
            Q(source__title__icontains=query) | Q(target__title__icontains=query)
        )
    page = Paginator(suggestions, 25).get_page(request.GET.get("page"))
    filters = urlencode(
        {
            k: v
            for k, v in (("status", status), ("relation", relation), ("q", query))
            if v
        }
    )
    return _page(
        request,
        "seo/links.html",
        title="Interlinking review",
        tab="links",
        subtitle="Links between brands, models, chipsets, flashing tools, guides and posts.",
        context={
            "page": page,
            "status": status,
            "relation": relation,
            "q": query,
            "filters": filters,
            "statuses": LinkSuggestion.Status.choices,
            "relations": sorted(
                interlinking.RELATION_LABELS.items(), key=lambda i: i[1]
            ),
            "relation_labels": interlinking.RELATION_LABELS,
            "counts": interlinking.status_counts(),
            "can_change": request.user.has_perm("seo.change_linksuggestion"),
            "next_url": request.get_full_path(),
        },
    )


@require_POST
@staff_only
def links_generate(request: HttpRequest) -> HttpResponse:
    _require(request, "seo.change_linksuggestion")
    report = interlinking.generate_proposals()
    messages.success(
        request,
        f"Checked {report.entities} entities: {report.created} new proposals, "
        f"{report.updated} refreshed, {report.retired} retired, "
        f"{report.skipped_budget} held back by the link budget.",
    )
    return redirect(_next_url(request, reverse("seo:links")))


@require_POST
@staff_only
def link_decide(request: HttpRequest, pk: int, decision: str) -> HttpResponse:
    _require(request, "seo.change_linksuggestion")
    suggestion = get_object_or_404(LinkSuggestion, pk=pk)
    try:
        interlinking.decide(suggestion, decision, request.user)
    except interlinking.DecisionError:
        raise Http404 from None
    messages.success(request, f"Link {_PAST[decision]}: {suggestion.anchor}.")
    return redirect(_next_url(request, reverse("seo:links")))


@require_POST
@staff_only
def links_bulk(request: HttpRequest) -> HttpResponse:
    _require(request, "seo.change_linksuggestion")
    ids = [int(value) for value in request.POST.getlist("ids") if value.isdigit()]
    if not ids:
        messages.warning(request, "Select at least one proposal.")
    else:
        try:
            changed = interlinking.decide_many(
                ids, request.POST.get("decision", ""), request.user
            )
        except interlinking.DecisionError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, f"Updated {changed} proposal(s).")
    return redirect(_next_url(request, reverse("seo:links")))


@require_GET
@staff_only
def guides(request: HttpRequest) -> HttpResponse:
    kind = request.GET.get("kind", "model")
    kind = kind if kind in {"model", "chipset"} else "model"
    only_missing = request.GET.get("missing") == "1"
    rows = (
        guides_service.model_rows(only_missing=only_missing)
        if kind == "model"
        else guides_service.chipset_rows(only_missing=only_missing)
    )
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return _page(
        request,
        "seo/guides.html",
        title="Flash guide coverage",
        tab="guides",
        subtitle="One 'how to flash' guide per model and one per chipset, with the right tools.",
        context={
            "page": page,
            "kind": kind,
            "only_missing": only_missing,
            "summary": guides_service.summary(),
            "filters": urlencode(
                {"kind": kind, **({"missing": "1"} if only_missing else {})}
            ),
            "can_generate": request.user.has_perm("seo.change_issue"),
            "next_url": request.get_full_path(),
        },
    )


@require_POST
@staff_only
def guides_generate(request: HttpRequest) -> HttpResponse:
    _require(request, "seo.change_issue")
    kind = request.POST.get("kind", "model")
    target = request.POST.get("pk", "")
    if kind == "chipset":
        rows = guides_service.chipset_rows(only_missing=not target)
    else:
        kind = "model"
        rows = guides_service.model_rows(only_missing=not target)
    if target:
        rows = [row for row in rows if str(row.pk) == target]
    rows = rows[: guides_service.MAX_GENERATE]
    if not rows:
        messages.info(request, "No guides need generating.")
        return redirect(_next_url(request, reverse("seo:guides")))
    ids = [row.pk for row in rows]
    try:
        synced = guides_service.generate(
            model_ids=ids if kind == "model" else None,
            chipset_ids=ids if kind == "chipset" else None,
        )
    except guides_service.GuideTaskUnavailable as error:
        messages.error(request, str(error))
    else:
        scan.rescan_paths([row.url for row in rows])
        messages.success(
            request, f"Refreshed {synced} guide post(s) and re-audited their pages."
        )
    return redirect(_next_url(request, reverse("seo:guides")))


@require_GET
@staff_only
def ledger_view(request: HttpRequest) -> HttpResponse:
    status = request.GET.get("status", "")
    entries = ChangeLedger.objects.all()
    if status in ChangeLedger.Status.values:
        entries = entries.filter(status=status)
    return _page(
        request,
        "seo/ledger.html",
        title="Change ledger",
        tab="ledger",
        subtitle="Every SEO change with before and after values, and rollback.",
        context={
            "entries": entries[:200],
            "status": status,
            "statuses": ChangeLedger.Status.choices,
            "can_approve": request.user.has_perm("seo.approve_changeledger"),
            "can_rollback": request.user.has_perm("seo.rollback_changeledger"),
        },
    )


_LEDGER_ACTIONS = {
    "approve": ("seo.approve_changeledger", ledger.approve),
    "reject": ("seo.approve_changeledger", ledger.reject),
    "rollback": ("seo.rollback_changeledger", ledger.rollback),
}


@require_POST
@staff_only
def ledger_action(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    if action not in _LEDGER_ACTIONS:
        raise Http404
    permission, operation = _LEDGER_ACTIONS[action]
    if not request.user.has_perm(permission):
        raise PermissionDenied
    entry = get_object_or_404(ChangeLedger, pk=pk)
    try:
        result = operation(entry, request.user)
    except ledger.LedgerError as error:
        messages.error(request, str(error))
    else:
        if action == "rollback":
            fixes.after_rollback(result)
        level = (
            messages.error
            if result.status == ChangeLedger.Status.FAILED
            else messages.success
        )
        level(
            request,
            f"Change {result.pk} is now {ChangeLedger.Status(result.status).label}.",
        )
    return redirect("seo:ledger")


@require_GET
@staff_only
def jobs_view(request: HttpRequest) -> HttpResponse:
    pending = SEOJob.objects.filter(status=SEOJob.Status.PENDING).count()
    return _page(
        request,
        "seo/jobs.html",
        title="SEO jobs",
        tab="jobs",
        subtitle="Content changes queue jobs; process them here without Celery.",
        context={
            "jobs": SEOJob.objects.all()[:200],
            "pending": pending,
            "failed": SEOJob.objects.filter(status=SEOJob.Status.FAILED).count(),
            "can_process": request.user.has_perm("seo.change_seojob"),
        },
    )


@require_POST
@staff_only
def process_jobs(request: HttpRequest) -> HttpResponse:
    if not request.user.has_perm("seo.change_seojob"):
        raise PermissionDenied
    counts = jobs.process_queue_now()
    remaining = counts.pop("remaining", 0)
    text = "Processed: " + (
        ", ".join(f"{name}={total}" for name, total in counts.items() if total)
        or "nothing was due"
    )
    if remaining:
        messages.warning(request, f"{text}. {remaining} still pending; run again.")
    else:
        messages.success(request, f"{text}. The queue is empty.")
    return redirect(_next_url(request, reverse("seo:jobs")))


@require_POST
@staff_only
def start_crawl(request: HttpRequest) -> HttpResponse:
    if not request.user.has_perm("seo.run_crawl"):
        raise PermissionDenied
    try:
        run = run_crawl(user=request.user)
    except CrawlUnavailable as reason:
        messages.error(request, str(reason))
    else:
        messages.success(
            request,
            f"Crawl {run.pk} finished: {run.pages_crawled} pages, "
            f"{run.pages_with_errors} with errors.",
        )
    return redirect("seo:dashboard")


@staff_only
def settings_view(request: HttpRequest) -> HttpResponse:
    row = SEOSettings.get_solo()
    before = ledger.serialize(row)
    form = SEOSettingsForm(request.POST or None, instance=row)
    if request.method == "POST":
        if not request.user.has_perm("seo.change_seosettings"):
            raise PermissionDenied
        if form.is_valid():
            saved = form.save()
            ledger.record_manual(
                user=request.user,
                action=ChangeLedger.Action.UPDATE,
                instance=saved,
                before=before,
                after=ledger.serialize(saved),
                reason="Changed in the SEO settings page",
            )
            messages.success(request, "SEO settings saved.")
            return redirect("seo:settings")
    return _page(
        request,
        "seo/settings.html",
        title="SEO settings",
        tab="settings",
        context={
            "form": form,
            "can_change": request.user.has_perm("seo.change_seosettings"),
        },
    )
