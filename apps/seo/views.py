"""Staff SEO Control Center: thin views over the services in this app."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from . import jobs, ledger
from .crawler import CrawlUnavailable, run_crawl
from .forms import SEOSettingsForm
from .integrations.status import integration_statuses
from .inventory import build_inventory, inspect_url
from .models import ChangeLedger, CrawlRun, Issue, SEOJob, SEOSettings
from .sitemap_registry import get_sitemaps

staff_only: Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]] = (
    staff_member_required(login_url="admin:login")
)


def _page(request: HttpRequest, template: str, context: dict[str, Any]) -> HttpResponse:
    base = admin.site.each_context(request)
    return render(request, template, {**base, "seo_nav": _nav(), **context})


def _nav() -> list[tuple[str, str]]:
    return [
        ("seo:dashboard", "Overview"),
        ("seo:inventory", "Page inventory"),
        ("seo:inspect", "Page inspector"),
        ("seo:issues", "Issues"),
        ("seo:ledger", "Change ledger"),
        ("seo:jobs", "Jobs"),
        ("seo:settings", "Settings"),
    ]


@require_GET
@staff_only
def dashboard(request: HttpRequest) -> HttpResponse:
    sections = [
        {"name": name, "urls": sitemap_class().paginator.count}
        for name, sitemap_class in get_sitemaps().items()
    ]
    latest = CrawlRun.objects.filter(status=CrawlRun.Status.COMPLETED).first()
    by_severity = dict(
        Issue.objects.filter(status=Issue.Status.OPEN)
        .values_list("severity")
        .annotate(total=Count("pk"))
    )
    return _page(
        request,
        "seo/dashboard.html",
        {
            "title": "SEO Control Center",
            "seo_settings": SEOSettings.get_solo(),
            "sections": sections,
            "latest_run": latest,
            "recent_runs": CrawlRun.objects.all()[:5],
            "open_errors": by_severity.get(Issue.Severity.ERROR, 0),
            "open_warnings": by_severity.get(Issue.Severity.WARNING, 0),
            "pending_changes": ChangeLedger.objects.filter(
                status=ChangeLedger.Status.PROPOSED
            ).count(),
            "failed_jobs": SEOJob.objects.filter(status=SEOJob.Status.FAILED).count(),
            "pending_jobs": SEOJob.objects.filter(status=SEOJob.Status.PENDING).count(),
            "integrations": integration_statuses(),
            "can_crawl": request.user.has_perm("seo.run_crawl"),
        },
    )


@require_GET
@staff_only
def inventory(request: HttpRequest) -> HttpResponse:
    items = build_inventory()
    return _page(
        request,
        "seo/inventory.html",
        {
            "title": "Page inventory",
            "items": items,
            "indexable_total": sum(1 for item in items if item.indexable),
            "conflict_total": sum(1 for item in items if item.conflicts),
        },
    )


@require_GET
@staff_only
def inspect(request: HttpRequest) -> HttpResponse:
    url = request.GET.get("url", "").strip()
    report = inspect_url(url) if url else None
    return _page(
        request,
        "seo/inspect.html",
        {"title": "Page inspector", "url": url, "report": report},
    )


@require_GET
@staff_only
def issues(request: HttpRequest) -> HttpResponse:
    queryset = Issue.objects.filter(status=Issue.Status.OPEN)
    severity = request.GET.get("severity", "")
    code = request.GET.get("code", "")
    if severity in Issue.Severity.values:
        queryset = queryset.filter(severity=severity)
    if code:
        queryset = queryset.filter(code=code)
    context = {
        "title": "SEO issues",
        "issues": queryset[:200],
        "severity": severity,
        "code": code,
        "codes": Issue.objects.filter(status=Issue.Status.OPEN)
        .order_by("code")
        .values_list("code", flat=True)
        .distinct(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "seo/partials/issue_rows.html", context)
    return _page(request, "seo/issues.html", context)


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
        {
            "title": "Change ledger",
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
    return _page(
        request,
        "seo/jobs.html",
        {
            "title": "SEO jobs",
            "jobs": SEOJob.objects.all()[:200],
            "pending": SEOJob.objects.filter(status=SEOJob.Status.PENDING).count(),
        },
    )


@require_POST
@staff_only
def process_jobs(request: HttpRequest) -> HttpResponse:
    if not request.user.has_perm("seo.change_seojob"):
        raise PermissionDenied
    counts = jobs.process_pending()
    messages.info(
        request, "Processed: " + (", ".join(f"{k}={v}" for k, v in counts.items()))
    )
    return redirect("seo:jobs")


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
        {
            "title": "SEO settings",
            "form": form,
            "can_change": request.user.has_perm("seo.change_seosettings"),
        },
    )
