"""Staff pages of the distribution engine, rendered in the staff console shell.

Every page checks the model permission it needs; every state change is a CSRF-protected
POST that redirects back (no native confirm dialogs, no inline scripts).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from importlib.util import find_spec
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db.models import Count, Sum
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.core.admin_pages import render_admin_page
from apps.distribution import (
    analytics,
    catalog,
    channels,
    feeds,
    library,
    media,
    news,
    services,
    syndication,
    videos,
)
from apps.distribution import content as content_module
from apps.distribution.constants import KIND_INSTAGRAM, KIND_TIKTOK, KIND_YOUTUBE
from apps.distribution.feed_views import ENABLED_CACHE_KEY
from apps.distribution.forms import FeedSettingsForm
from apps.distribution.models import (
    Channel,
    DistributionSettings,
    FeedConfig,
    FeedStat,
    PingOutbox,
    ShareJob,
    VideoJob,
)
from apps.distribution.video import capabilities as video_capabilities
from apps.distribution.video import packs, sources

PERM = "distribution.{}_{}"
EPOCH = feeds.EPOCH
VIDEO_CHANNEL_KINDS = (KIND_TIKTOK, KIND_YOUTUBE, KIND_INSTAGRAM)
TABS = (
    ("dashboard", "Dashboard", "admin:distribution_channel_dashboard"),
    ("channels", "Channels", "admin:distribution_channel_changelist"),
    ("videos", "Videos", "admin:distribution_channel_videos"),
    ("library", "Media library", "admin:distribution_channel_media_library"),
    ("calendar", "Calendar", "admin:distribution_channel_calendar"),
    ("review", "Approvals", "admin:distribution_channel_review"),
    ("handoff", "Hand-off", "admin:distribution_channel_handoff"),
    ("feeds", "Feeds & Google News", "admin:distribution_channel_feeds"),
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
    elif job.status == ShareJob.Status.HANDOFF:
        messages.info(
            request,
            f"Test post prepared for {channel.name}. It is not sent: post it by hand from Hand-off.",
        )
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
        reverse("admin:distribution_channel_handoff"),
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


# ------------------------------------------------------------ feeds & news ----


def _push_status() -> list[dict[str, str]]:
    """What reaches readers and Android devices, automatic or manual, honestly."""
    cfg = DistributionSettings.get_solo()
    hub = syndication.hub_url()
    rows = [
        {
            "name": "RSS, Atom and JSON feeds",
            "state": "on" if cfg.feeds_enabled else "off",
            "note": "Automatic once a reader subscribes. Readers poll; Android reaches them through a feed-reader app.",
        },
        {
            "name": "WebSub push",
            "state": "on" if hub else "off",
            "note": "Automatic pings to your hub on every publish, so subscribed readers hear sooner."
            if hub
            else "Not enabled: set a WebSub hub (https) in the settings below.",
        },
        {
            "name": "IndexNow (Bing, Yandex)",
            "state": "on" if syndication.indexnow_active() else "off",
            "note": "Automatic pings through the SEO settings."
            if syndication.indexnow_active()
            else "Not enabled: turn on IndexNow and set its key in the SEO settings. Google does not use IndexNow.",
        },
    ]
    push_ready = bool(
        getattr(settings, "VAPID_PUBLIC_KEY", "")
        and getattr(settings, "VAPID_PRIVATE_KEY", "")
        and find_spec("pywebpush") is not None
    )
    rows.append(
        {
            "name": "Web Push (Android Chrome)",
            "state": "on" if push_ready else "off",
            "note": "Account notifications only (owned by Users); feeds do not send browser push."
            if push_ready
            else "Not enabled: needs VAPID keys and the pywebpush package, which this site does not have installed.",
        }
    )
    rows.append(
        {
            "name": "Google News and Discover",
            "state": "manual",
            "note": "Not automatic. Google decides after you register in Publisher Center and verify Search Console.",
        }
    )
    return rows


def _feed_rows() -> list[dict[str, Any]]:
    configs = {row.key: row for row in FeedConfig.objects.all()}
    news_ok = feeds.news_available()
    rows: list[dict[str, Any]] = []
    for key, title, description in feeds.FIXED:
        config = configs.get(key)
        rows.append(
            {
                "key": key,
                "title": title,
                "description": description,
                "config": config,
                "enabled": config.enabled if config else True,
                "available": news_ok or key != feeds.NEWS_KEY,
                "urls": [
                    {"fmt": fmt, "label": label, "url": feeds.feed_url(key, fmt)}
                    for fmt, (_mime, label) in feeds.FORMATS.items()
                ],
                "dynamic": False,
            }
        )
    dynamic = [
        row
        for row in configs.values()
        if row.key.split("/", 1)[0] in {"brand", "chipset", "tag"}
    ]
    dynamic.sort(key=lambda row: row.last_fetched_at or EPOCH, reverse=True)
    for config in dynamic[:15]:
        rows.append(
            {
                "key": config.key,
                "title": config.key.replace("/", ": "),
                "description": "On demand",
                "config": config,
                "enabled": config.enabled,
                "available": True,
                "urls": [
                    {"fmt": fmt, "label": label, "url": feeds.feed_url(config.key, fmt)}
                    for fmt, (_mime, label) in feeds.FORMATS.items()
                ],
                "dynamic": True,
            }
        )
    return rows


def _feeds_context() -> dict[str, Any]:
    cfg = DistributionSettings.get_solo()
    since = timezone.now() - timedelta(hours=24)
    totals = FeedStat.objects.filter(bucket__gte=since).aggregate(
        fetches=Sum("fetches"), not_modified=Sum("not_modified")
    )
    return {
        "settings": cfg,
        "rows": _feed_rows(),
        "recent": FeedStat.objects.all()[:12],
        "fetches_24h": totals["fetches"] or 0,
        "not_modified_24h": totals["not_modified"] or 0,
        "pings": PingOutbox.objects.all()[:15],
        "ping_summary": syndication.summary(),
    }


def feeds_page(request: HttpRequest) -> HttpResponse:
    _need(request, "view", "channel")
    context = _feeds_context()
    if (
        request.headers.get("HX-Request")
        and request.headers.get("HX-Target") == "dist-feeds-live"
    ):
        return render(
            request,
            "admin/distribution/_feeds_live.html",
            {
                **context,
                "can_edit": request.user.has_perm(
                    PERM.format("change", "distributionsettings")
                ),
                "can_change": request.user.has_perm("distribution.change_sharejob"),
            },
        )
    return _page(
        request,
        "admin/distribution/feeds.html",
        title="Feeds & Google News",
        subtitle="Public RSS, Atom and JSON feeds, push pings and Google News readiness.",
        section="feeds",
        context={
            **context,
            "news": news.status(),
            "checks": news.discover_scan(),
            "seo_scan_url": news.seo_scan_url(),
            "push": _push_status(),
            "form": FeedSettingsForm(instance=context["settings"]),
            "can_edit": request.user.has_perm(
                PERM.format("change", "distributionsettings")
            ),
            "feeds_index": reverse("feeds:index"),
        },
    )


def _feeds_back() -> HttpResponseRedirect:
    return redirect("admin:distribution_channel_feeds")


@require_POST
def feeds_settings(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "distributionsettings")
    form = FeedSettingsForm(request.POST, instance=DistributionSettings.get_solo())
    if form.is_valid():
        form.save()
        cache.delete(ENABLED_CACHE_KEY)
        messages.success(request, "Feed settings saved.")
    else:
        for errors in form.errors.values():
            messages.error(request, "; ".join(str(error) for error in errors))
    return _feeds_back()


@require_POST
def feeds_toggle(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "distributionsettings")
    key = request.POST.get("key", "")
    if feeds.spec_for(key) is None:
        raise Http404
    config, _created = FeedConfig.objects.get_or_create(key=key)
    config.enabled = not config.enabled
    config.save(update_fields=["enabled"])
    messages.success(request, f"Feed {key} is now {'on' if config.enabled else 'off'}.")
    return _feeds_back()


@require_POST
def feeds_validate(request: HttpRequest) -> HttpResponse:
    """Build the feed in all three formats and check what readers require."""
    _need(request, "view", "channel")
    key = request.POST.get("key", "")
    spec = feeds.spec_for(key)
    if spec is None:
        raise Http404
    problems: list[str] = []
    for fmt in feeds.FORMATS:
        built = feeds.build(spec, fmt)
        problems.extend(f"{fmt}: {p}" for p in feeds.validate(built.body, fmt))
    summary = (
        "Valid in RSS, Atom and JSON." if not problems else "; ".join(problems)[:300]
    )
    if request.user.has_perm(PERM.format("change", "distributionsettings")):
        config, _created = FeedConfig.objects.get_or_create(key=key)
        config.last_validation = summary
        config.last_validated_at = timezone.now()
        config.save(update_fields=["last_validation", "last_validated_at"])
    (messages.success if not problems else messages.warning)(
        request, f"{key}: {summary}"
    )
    return _feeds_back()


@require_POST
def feeds_news_step(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "distributionsettings")
    step = request.POST.get("step", "")
    if not news.set_step(step, request.POST.get("done") == "1"):
        raise Http404
    messages.success(
        request,
        "Google News checklist updated. This is your own note: Google confirms nothing.",
    )
    return _feeds_back()


@require_POST
def feeds_ping(request: HttpRequest, pk: int, action: str) -> HttpResponse:
    _need(request, "change", "distributionsettings")
    ping = get_object_or_404(PingOutbox, pk=pk)
    done = {"retry": syndication.retry, "cancel": syndication.cancel}.get(action)
    if done is None:
        raise Http404
    if done(ping):
        messages.success(request, f"Ping {action} done.")
    else:
        messages.warning(request, "That ping can no longer change.")
    return _feeds_back()


@require_POST
def feeds_ping_run(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "distributionsettings")
    counts = syndication.process_due()
    messages.success(
        request,
        f"Pings: {counts['sent']} sent, {counts['retried']} to retry, {counts['failed']} failed.",
    )
    return _feeds_back()


# ----------------------------------------------------------- motion videos ----

VIDEO_PAGE_SIZE = 24
LIBRARY_BATCH_LIMIT = 10
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _video_filters(request: HttpRequest) -> dict[str, Any]:
    status = request.GET.get("status", "")
    if status not in {s.value for s in VideoJob.Status}:
        status = ""
    kind = request.GET.get("kind", "")
    if kind not in sources.VIDEO_KINDS:
        kind = ""
    return {"status": status, "kind": kind, "q": request.GET.get("q", "").strip()[:80]}


def _videos_context(request: HttpRequest) -> dict[str, Any]:
    filters = _video_filters(request)
    rows = VideoJob.objects.all()
    if filters["status"]:
        rows = rows.filter(status=filters["status"])
    if filters["kind"]:
        rows = rows.filter(content_kind=filters["kind"])
    if filters["q"]:
        rows = rows.filter(title__icontains=filters["q"])
    jobs = list(rows[:VIDEO_PAGE_SIZE])
    counts = {
        row["status"]: row["n"]
        for row in VideoJob.objects.values("status").annotate(n=Count("id"))
    }
    distribution_settings = DistributionSettings.get_solo()
    budget = videos.auto_budget()
    return {
        "jobs": [
            {
                "job": job,
                "packs": packs.packs_for(job.content_kind),
                "pack_label": packs.PACKS[job.template].label
                if job.template in packs.PACKS
                else job.template,
            }
            for job in jobs
        ],
        "filters": filters,
        "counts": counts,
        "statuses": VideoJob.Status.choices,
        "kinds": sources.VIDEO_KINDS,
        "busy": any(j.status == VideoJob.Status.RENDERING for j in jobs),
        "channels": Channel.objects.filter(
            enabled=True, kind__in=VIDEO_CHANNEL_KINDS
        ).order_by("name"),
        "can_edit": request.user.has_perm("distribution.change_videojob"),
        "can_add": request.user.has_perm("distribution.add_videojob"),
        "budget": budget,
        "budget_used": max(distribution_settings.video_daily_cap - budget, 0),
        "settings": distribution_settings,
    }


def media_library(request: HttpRequest) -> HttpResponse:
    """Render a bounded inventory of public content and its generated media."""
    _need(request, "view", "videojob")
    kind = request.GET.get("kind", "firmware")
    if kind not in library.LIBRARY_KINDS:
        kind = "firmware"
    firmware_type = request.GET.get("firmware_type", "all")
    if firmware_type not in library.FIRMWARE_TYPES:
        firmware_type = "all"
    page_value = request.GET.get("page", "1").strip()
    page_number = (
        int(page_value)
        if page_value.isascii() and page_value.isdigit() and len(page_value) <= 6
        else 1
    )
    page = library.get_page(kind, page_number, firmware_type)
    states = library.statuses(page.entries)
    return _page(
        request,
        "admin/distribution/media_library.html",
        title="Media library",
        subtitle=(
            "Public firmware, editorial posts, guides, tools and catalog spotlights. "
            "Historical generation is manual, bounded and idempotent."
        ),
        section="library",
        context={
            "entries": [
                {"entry": entry, "status": states[entry.key]} for entry in page.entries
            ],
            "kind": kind,
            "kinds": library.LIBRARY_KINDS,
            "firmware_type": firmware_type,
            "firmware_types": library.FIRMWARE_TYPES,
            "page": page,
            "batch_limit": LIBRARY_BATCH_LIMIT,
            "video_budget": videos.auto_budget(),
            "video_cap": DistributionSettings.get_solo().video_daily_cap,
            "card_capable": media.capabilities()["cards"],
            "can_generate_cards": media.capabilities()["cards"]
            and request.user.has_perm("distribution.add_mediaasset")
            and request.user.has_perm("distribution.change_mediaasset"),
            "can_create_videos": request.user.has_perm("distribution.add_videojob"),
        },
    )


def _library_back(request: HttpRequest) -> HttpResponseRedirect:
    """Return to the requested media-library filters after a staff action."""
    kind = request.POST.get("kind", "")
    firmware_type = request.POST.get("firmware_type", "")
    page = request.POST.get("page", "")
    params = {}
    if kind in library.LIBRARY_KINDS:
        params["kind"] = kind
    if firmware_type in library.FIRMWARE_TYPES:
        params["firmware_type"] = firmware_type
    if page.isdigit():
        params["page"] = page
    suffix = f"?{urlencode(params)}" if params else ""
    return redirect(f"{reverse('admin:distribution_channel_media_library')}{suffix}")


def _create_library_cards(selected: list[str]) -> tuple[int, int, int]:
    created = skipped = failed = 0
    for selected_key in selected:
        kind, separator, ref = selected_key.partition("|")
        entry = library.resolve_entry(kind, ref) if separator else None
        if entry is None:
            skipped += 1
            continue
        spec = media.MediaSpec(
            entry.media_kind,
            entry.media_ref,
            entry.title,
            entry.label,
            entry.lines,
            services.site_name(),
        )
        horizontal = media.ensure_card(spec)
        vertical = media.ensure_card(spec, vertical=True)
        if horizontal.asset is None or vertical.asset is None:
            failed += 1
        else:
            created += 1
    return created, skipped, failed


def _create_library_videos(selected: list[str]) -> tuple[int, int, int, int, bool]:
    created = existing = skipped = failed = 0
    cap_reached = False
    for selected_key in selected:
        kind, separator, ref = selected_key.partition("|")
        entry = library.resolve_entry(kind, ref) if separator else None
        if entry is None or not entry.video_kind:
            skipped += 1
            continue
        source = sources.load(entry.video_kind, entry.video_ref)
        if source is None:
            skipped += 1
            continue
        job, reason = videos.create(
            entry.video_kind,
            entry.video_ref,
            auto=True,
            source=source,
        )
        if job is None:
            if "daily limit" in reason.lower():
                cap_reached = True
                break
            failed += 1
        elif reason:
            existing += 1
        else:
            created += 1
    return created, existing, skipped, failed, cap_reached


def _report_library_cards(
    request: HttpRequest, created: int, failed: int, skipped: int
) -> None:
    if created:
        messages.success(
            request,
            f"Horizontal and vertical share cards are ready for {created} item(s).",
        )
    if failed:
        messages.error(
            request,
            f"Card generation failed for {failed} item(s); "
            "check the rendering capability report.",
        )
    if skipped:
        messages.warning(
            request,
            f"Skipped {skipped} item(s) that are no longer public or do not "
            "support this action.",
        )
    if not (created or skipped or failed):
        messages.info(request, "No media changes were needed.")


def _report_library_videos(
    request: HttpRequest,
    result: tuple[int, int, int, int, bool],
) -> None:
    created, existing, skipped, failed, cap_reached = result
    if created or existing:
        messages.success(
            request,
            f"Created {created} automatic video draft(s); {existing} already existed.",
        )
    if cap_reached:
        messages.warning(
            request,
            "The daily automatic draft cap is reached "
            f"({videos.auto_budget()} remaining).",
        )
    if skipped:
        messages.warning(
            request,
            f"Skipped {skipped} item(s) that are no longer public or do not "
            "support this action.",
        )
    if failed:
        messages.error(request, f"Could not create {failed} video draft(s).")
    if not (created or existing or skipped or failed or cap_reached):
        messages.info(request, "No media changes were needed.")


@require_POST
def media_library_bulk(request: HttpRequest) -> HttpResponse:
    """Generate bounded media batches for currently public library entries."""
    action = request.POST.get("action", "")
    if action not in {"cards", "videos"}:
        raise Http404
    if action == "cards":
        _need(request, "add", "mediaasset")
        _need(request, "change", "mediaasset")
    else:
        _need(request, "add", "videojob")

    selected = list(dict.fromkeys(request.POST.getlist("selected")))
    if not selected:
        messages.warning(request, "Select at least one public item.")
        return _library_back(request)
    if len(selected) > LIBRARY_BATCH_LIMIT:
        messages.error(
            request,
            f"Choose no more than {LIBRARY_BATCH_LIMIT} items per batch.",
        )
        return _library_back(request)

    if action == "cards":
        created, skipped, failed = _create_library_cards(selected)
        _report_library_cards(request, created, failed, skipped)
    else:
        result = _create_library_videos(selected)
        _report_library_videos(request, result)
    return _library_back(request)


def videos_page(request: HttpRequest) -> HttpResponse:
    _need(request, "view", "videojob")
    context = _videos_context(request)
    if (
        request.headers.get("HX-Request")
        and request.headers.get("HX-Target") == "dist-videos-live"
    ):
        return render(request, "admin/distribution/_videos_live.html", context)
    report = video_capabilities.report()
    return _page(
        request,
        "admin/distribution/videos.html",
        title="Videos",
        subtitle="Motion videos for TikTok, YouTube Shorts and Reels. Nothing is posted until you approve it.",
        section="videos",
        context={
            **context,
            "capabilities_report": report,
            "can_render": all(c.ok for c in report if c.required),
            "pack_options": packs.USER_PACKS,
        },
    )


def _videos_back() -> HttpResponseRedirect:
    return redirect("admin:distribution_channel_videos")


def _video(pk: int) -> VideoJob:
    return get_object_or_404(VideoJob, pk=pk)


@require_POST
def video_create(request: HttpRequest) -> HttpResponse:
    _need(request, "add", "videojob")
    kind = request.POST.get("kind", "")
    ref = request.POST.get("ref", "").strip()[:120]
    hook = request.POST.get("hook", "A")
    job, reason = videos.create(
        kind, ref, pack_slug=request.POST.get("template", ""), hook=hook
    )
    if job is None:
        messages.error(request, reason)
    elif request.POST.get("render") == "1" and videos.request_render(job):
        messages.success(
            request, f"Rendering “{job.title}”. It appears here when ready."
        )
    else:
        messages.success(request, reason or f"Draft created for “{job.title}”.")
    return _videos_back()


@require_POST
def video_render(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "videojob")
    job = _video(pk)
    target, reason = videos.rerender(
        job,
        request.user,
        pack_slug=request.POST.get("template", ""),
        hook=request.POST.get("hook", ""),
    )
    if target is None or reason:
        messages.warning(request, reason or "Nothing to render.")
    else:
        messages.success(request, f"Rendering “{target.title}” again.")
    return _videos_back()


@require_POST
def video_decide(request: HttpRequest, pk: int, decision: str) -> HttpResponse:
    _need(request, "change", "videojob")
    job = _video(pk)
    if decision == "approve":
        done = videos.approve(job, request.user)
        verb = "Approved"
    elif decision == "reject":
        done = videos.reject(job, request.user, request.POST.get("note", ""))
        verb = "Rejected"
    else:
        raise Http404
    if done:
        messages.success(request, f"{verb} “{job.title}”.")
    else:
        messages.warning(request, "That video is not waiting for a decision.")
    return _videos_back()


@require_POST
def video_schedule(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "videojob")
    job = _video(pk)
    ids = [int(v) for v in request.POST.getlist("channels") if v.isdigit()]
    count = videos.schedule(job, ids)
    if count:
        messages.success(
            request,
            f"Queued {count} post(s) for “{job.title}”; they follow each channel's schedule.",
        )
    else:
        messages.warning(
            request,
            "Nothing was queued: the video must be approved and a channel chosen.",
        )
    return _videos_back()


@require_POST
def video_bulk(request: HttpRequest) -> HttpResponse:
    _need(request, "change", "videojob")
    action = request.POST.get("action", "")
    picked = VideoJob.objects.filter(
        pk__in=[int(v) for v in request.POST.getlist("selected") if v.isdigit()]
    )
    done = 0
    for job in picked:
        if action == "approve":
            done += videos.approve(job, request.user)
        elif action == "reject":
            done += videos.reject(job, request.user, "Bulk rejected")
        elif action == "render":
            target, reason = videos.rerender(job, request.user)
            done += int(target is not None and not reason)
        else:
            raise Http404
    messages.success(request, f"{done} video(s) updated.")
    return _videos_back()


def video_media(request: HttpRequest, pk: int, part: str) -> HttpResponse:
    """Staff-only stream of a video or its poster (never a public address), with ranges."""
    _need(request, "view", "videojob")
    job = _video(pk)
    name = {"video": job.video_name, "poster": job.poster_name}.get(part, "")
    if not name or not default_storage.exists(name):
        raise Http404
    mime = "video/mp4" if part == "video" else "image/jpeg"
    size = default_storage.size(name)
    start, end = 0, size - 1
    status = 200
    match = RANGE_RE.match(request.headers.get("Range", ""))
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else size - 1
        else:
            start = max(0, size - int(match.group(2)))
        end = min(end, size - 1)
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        status = 206
    with default_storage.open(name, "rb") as handle:
        handle.seek(start)
        body = handle.read(end - start + 1)
    response = HttpResponse(body, status=status, content_type=mime)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(len(body))
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


# ---------------------------------------------------------------- platforms ----


def _existing_by_kind() -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = defaultdict(list)
    for channel in Channel.objects.order_by("name"):
        label, tone = catalog.STATE_LABELS[channel.state]
        found[channel.kind].append(
            {
                "name": channel.name,
                "url": reverse("admin:distribution_channel_change", args=[channel.pk]),
                "state": label,
                "tone": tone,
            }
        )
    return found


def _picker_context(request: HttpRequest) -> dict[str, Any]:
    query = request.GET.get("q", "").strip()[:80]
    category = request.GET.get("cat", "all")
    if category != "all" and category not in catalog.CATEGORY_BY_KEY:
        category = "all"
    existing = _existing_by_kind()
    cards = [
        catalog.card_for(kind, adapter, existing.get(kind, []))
        for kind, adapter in channels.adapters().items()
    ]
    visible = [card for card in cards if catalog.matches(card, query)]
    sections = [
        section
        for section in catalog.group(visible)
        if section.cards and category in ("all", section.category.key)
    ]
    connected = sorted(
        (
            c
            for c in visible
            if c.connected and (category == "all" or category in c.categories)
        ),
        key=lambda c: c.adapter.label,
    )
    return {
        "q": query,
        "cat": category,
        "sections": sections,
        "connected": connected,
        "menu": [
            {
                "category": section.category,
                "count": section.count,
                "current": section.category.key == category,
            }
            for section in catalog.group(visible)
        ],
        "total": len({c.kind for c in visible}),
        "add_url": reverse("admin:distribution_channel_add"),
    }


def platforms(request: HttpRequest) -> HttpResponse:
    _need(request, "add", "channel")
    context = {**_picker_context(request), "capabilities": media.capabilities()}
    if (
        request.headers.get("HX-Request")
        and request.headers.get("HX-Target") == "dist-picker-results"
    ):
        return render(request, "admin/distribution/_picker_results.html", context)
    return _page(
        request,
        "admin/distribution/platforms.html",
        title="Add a channel",
        subtitle="Choose where new firmware and posts should go. Channels stay off until you enable them.",
        section="channels",
        context=context,
    )


def requirements(request: HttpRequest, kind: str) -> HttpResponse:
    """What a platform needs and how to get it (a dialog over HTMX, a page otherwise)."""
    _need(request, "add", "channel")
    adapter = channels.get_adapter(kind)
    if adapter is None:
        raise Http404
    card = catalog.card_for(kind, adapter, _existing_by_kind().get(kind, []))
    context = {
        "card": card,
        "adapter": adapter,
        "info": card.info,
        "fields": adapter.fields,
        "connect_url": f"{reverse('admin:distribution_channel_add')}?kind={kind}",
    }
    if request.headers.get("HX-Request"):
        return render(request, "admin/distribution/_requirements.html", context)
    return _page(
        request,
        "admin/distribution/requirements.html",
        title=adapter.label,
        subtitle="What you need and how to get it.",
        section="channels",
        context=context,
    )


# ----------------------------------------------------------------- hand-off ----


def handoff(request: HttpRequest) -> HttpResponse:
    """Posts for assisted platforms (no API): copy-ready text, then "Mark as posted"."""
    _need(request, "view", "sharejob")
    jobs = (
        ShareJob.objects.filter(status=ShareJob.Status.HANDOFF)
        .select_related("channel")
        .order_by("created_at", "id")[:100]
    )
    rows = []
    for job in jobs:
        values = services.stored_values(job.channel)
        rows.append({"job": job, "link": values.get("channel_url", "")})
    return _page(
        request,
        "admin/distribution/handoff.html",
        title="Hand-off",
        subtitle="Posts for platforms without a posting API. Paste each one, then mark it posted.",
        section="handoff",
        context={"rows": rows},
    )


@require_POST
def job_posted(request: HttpRequest, pk: int) -> HttpResponse:
    _need(request, "change", "sharejob")
    job = get_object_or_404(ShareJob.objects.select_related("channel"), pk=pk)
    if services.mark_posted(job, request.user):
        messages.success(request, f"“{job.title}” marked as posted.")
    else:
        messages.warning(request, "That post was already handled.")
    return redirect("admin:distribution_channel_handoff")


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
