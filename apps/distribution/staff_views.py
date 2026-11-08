"""Staff pages of the distribution engine, rendered in the Unfold admin shell.

Every page checks the model permission it needs; every state change is a CSRF-protected
POST that redirects back (no native confirm dialogs, no inline scripts).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.core.admin_pages import render_admin_page
from apps.distribution import analytics, channels, media, services
from apps.distribution import content as content_module
from apps.distribution.models import Channel, ShareJob

PERM = "distribution.{}_{}"
TABS = (
    ("dashboard", "Dashboard", "admin:distribution_channel_dashboard"),
    ("channels", "Channels", "admin:distribution_channel_changelist"),
    ("calendar", "Calendar", "admin:distribution_channel_calendar"),
    ("review", "Approvals", "admin:distribution_channel_review"),
    ("queue", "Share queue", "admin:distribution_sharejob_changelist"),
    ("analytics", "Link analytics", "admin:distribution_channel_analytics"),
    ("log", "Log", "admin:distribution_sharelog_changelist"),
)
RECENT_SENT_DAYS = 3
CALENDAR_LIMIT = 300


def _need(request: HttpRequest, action: str, model: str) -> None:
    if not request.user.has_perm(PERM.format(action, model)):
        raise PermissionDenied


def _tabs(active: str) -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "url": reverse(name), "active": key == active}
        for key, label, name in TABS
    ]


def _page(
    request: HttpRequest,
    template: str,
    *,
    title: str,
    subtitle: str,
    section: str,
    context: dict[str, Any],
) -> HttpResponse:
    return render_admin_page(
        request,
        template,
        title=title,
        subtitle=subtitle,
        context={
            "tabs": _tabs(section),
            "can_change": request.user.has_perm("distribution.change_sharejob"),
            **context,
        },
    )


def _back(name: str) -> HttpResponseRedirect:
    return redirect(name)


# ---------------------------------------------------------------- dashboard ----


def dashboard(request: HttpRequest) -> HttpResponse:
    _need(request, "view", "channel")
    return _page(
        request,
        "admin/distribution/dashboard.html",
        title="Distribution",
        subtitle="Automatic sharing of new firmware and posts to your channels.",
        section="dashboard",
        context={
            **analytics.dashboard_stats(),
            "can_run": request.user.has_perm("distribution.change_channel"),
        },
    )


@require_POST
def run_queue(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "channel")
    summary = services.process_due_jobs()
    messages.success(
        request,
        f"Queue run: {summary.sent} sent, {summary.retried} to retry, "
        f"{summary.failed} failed, {summary.deferred} waiting for the platform gap.",
    )
    return _back("admin:distribution_channel_dashboard")


@require_POST
def channel_test(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "channel")
    channel = get_object_or_404(Channel, pk=pk)
    job = services.send_test(channel)
    if job.status == ShareJob.Status.SENT:
        messages.success(request, f"Test post delivered to {channel.name}.")
    else:
        messages.error(request, f"Test post to {channel.name} failed: {job.last_error}")
    return _back("admin:distribution_channel_dashboard")


@require_POST
def channel_check(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "channel")
    channel = get_object_or_404(Channel, pk=pk)
    outcome = services.check_connection(channel)
    detail = outcome.detail or outcome.code
    if outcome.ok:
        messages.success(request, f"{channel.name}: {detail}")
    elif outcome.code == "unsupported":
        messages.warning(request, f"{channel.name}: {detail}")
    else:
        messages.error(request, f"{channel.name}: connection failed. {detail}")
    return _back("admin:distribution_channel_dashboard")


# ----------------------------------------------------------------- calendar ----


def calendar(request: HttpRequest) -> HttpResponse:
    _need(request, "view", "sharejob")
    now = timezone.now()
    since = now - timedelta(days=RECENT_SENT_DAYS)
    jobs = (
        ShareJob.objects.filter(
            status__in=[*services.PENDING_STATES, ShareJob.Status.SENT],
        )
        .exclude(status=ShareJob.Status.SENT, sent_at__lt=since)
        .select_related("channel")
        .order_by("next_attempt_at", "id")[:CALENDAR_LIMIT]
    )
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        moment = (
            job.sent_at if job.status == ShareJob.Status.SENT else job.next_attempt_at
        )
        local = timezone.localtime(moment or job.next_attempt_at)
        grouped[local.date()].append(
            {
                "job": job,
                "time": local,
                "input": local.strftime("%Y-%m-%dT%H:%M"),
                "editable": job.status in services.PENDING_STATES,
            }
        )
    days = [{"date": day, "items": items} for day, items in sorted(grouped.items())]
    return _page(
        request,
        "admin/distribution/calendar.html",
        title="Content calendar",
        subtitle="Upcoming and just-sent posts, by day. Move or cancel what is waiting.",
        section="calendar",
        context={"days": days, "now": now},
    )


def _job_action_target(request: HttpRequest) -> str:
    nxt = request.POST.get("next", "")
    allowed = {
        reverse("admin:distribution_channel_calendar"),
        reverse("admin:distribution_channel_review"),
    }
    return nxt if nxt in allowed else reverse("admin:distribution_channel_calendar")


@require_POST
def job_cancel(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "sharejob")
    job = get_object_or_404(ShareJob.objects.select_related("channel"), pk=pk)
    if services.cancel_job(job, request.user):
        messages.success(request, f"Cancelled “{job.title}”.")
    else:
        messages.warning(request, "That post can no longer be cancelled.")
    return redirect(_job_action_target(request))


@require_POST
def job_reschedule(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "sharejob")
    job = get_object_or_404(ShareJob.objects.select_related("channel"), pk=pk)
    when = _parse_local(request.POST.get("when", ""))
    if when is None:
        messages.error(request, "Enter a valid date and time.")
    elif services.reschedule_job(job, when, request.user):
        messages.success(request, f"“{job.title}” moved to {when:%Y-%m-%d %H:%M}.")
    else:
        messages.warning(request, "That post can no longer be moved.")
    return redirect(_job_action_target(request))


def _parse_local(value: str) -> datetime | None:
    parsed = parse_datetime(value.strip()) if value.strip() else None
    if parsed is None:
        return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


# ------------------------------------------------------------------- review ----


def review(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "sharejob")
    pending = ShareJob.objects.filter(status=ShareJob.Status.PENDING).select_related(
        "channel"
    )
    rows = []
    for job in pending.order_by("next_attempt_at", "id")[:100]:
        adapter = job.channel.adapter
        rows.append(
            {
                "job": job,
                "limit": adapter.message_limit if adapter else 0,
                "time": timezone.localtime(job.next_attempt_at),
            }
        )
    return _page(
        request,
        "admin/distribution/review.html",
        title="Approval queue",
        subtitle="Posts from channels that need a staff decision. Edit the text, then approve or reject.",
        section="review",
        context={"rows": rows},
    )


@require_POST
def job_decide(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "sharejob")
    job = get_object_or_404(ShareJob.objects.select_related("channel"), pk=pk)
    decision = request.POST.get("decision", "")
    if decision == "approve":
        try:
            done = services.approve_job(
                job, request.user, text=request.POST.get("text")
            )
        except ValueError as error:
            messages.error(request, str(error))
            return redirect("admin:distribution_channel_review")
        if done:
            messages.success(request, f"Approved “{job.title}”.")
        else:
            messages.warning(request, "That post was already decided.")
    elif decision == "reject":
        if services.reject_job(job, request.user, request.POST.get("note", "")):
            messages.success(request, f"Rejected “{job.title}”.")
        else:
            messages.warning(request, "That post was already decided.")
    else:
        raise Http404
    return redirect("admin:distribution_channel_review")


# ---------------------------------------------------------------- analytics ----


def _days(request: HttpRequest) -> int:
    raw = request.GET.get("days", "30")
    return int(raw) if raw in {"7", "30", "90"} else 30


def analytics_page(request: HttpRequest) -> HttpResponse:
    _need(request, "view", "shortlink")
    days = _days(request)
    return _page(
        request,
        "admin/distribution/analytics.html",
        title="Link analytics",
        subtitle="Human clicks on tracked links. Bots and link previews are not counted; no personal data is stored.",
        section="analytics",
        context={
            "days": days,
            "ranges": (7, 30, 90),
            "channels": analytics.per_channel(days),
            "content": analytics.top_content(days, 10),
            "variants": analytics.per_variant(),
            "heatmap": analytics.heatmap(days),
            "total": analytics.total_clicks(days),
            "lifetime": analytics.lifetime_clicks(),
        },
    )


def analytics_export(request: HttpRequest) -> StreamingHttpResponse:
    _need(request, "view", "shortlink")
    response = StreamingHttpResponse(
        analytics.csv_rows(), content_type="text/csv; charset=utf-8"
    )
    response["Content-Disposition"] = 'attachment; filename="distribution-links.csv"'
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------- platforms ----


def platforms(request: HttpRequest) -> HttpResponse:
    _need(request, "add", "channel")
    cards = []
    for kind, adapter in sorted(
        channels.adapters().items(), key=lambda pair: pair[1].label
    ):
        cards.append(
            {
                "kind": kind,
                "adapter": adapter,
                "url": f"{reverse('admin:distribution_channel_add')}?kind={kind}",
                "fields": adapter.fields,
            }
        )
    return _page(
        request,
        "admin/distribution/platforms.html",
        title="Add a channel",
        subtitle="Pick a platform. Channels stay off until you enable them.",
        section="channels",
        context={"cards": cards, "capabilities": media.capabilities()},
    )


# ------------------------------------------------------------------ preview ----


def preview(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "view", "channel")
    channel = get_object_or_404(Channel, pk=pk)
    latest = (
        ShareJob.objects.filter(content_kind__in=("firmware", "blog"))
        .order_by("-created_at")
        .first()
    )
    real = (
        content_module.load(latest.content_kind, latest.object_ref) if latest else None
    )
    return _page(
        request,
        "admin/distribution/preview.html",
        title=f"Preview: {channel.name}",
        subtitle="Dry run: nothing is sent. Shows the post this channel would write.",
        section="channels",
        context={
            "channel": channel,
            "adapter": channel.adapter,
            "previews": [
                p
                for p in (
                    services.build_preview(channel, real) if real else None,
                    services.build_preview(channel),
                )
                if p is not None
            ],
        },
    )
