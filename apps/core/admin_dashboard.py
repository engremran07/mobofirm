"""Operations dashboard data of the staff console.

Counts are ORM aggregates computed per metric and cached briefly as plain numbers
(no per-user data, so the cache is shared); permission filtering happens per
request when a widget renders. A metric whose app or table cannot be read is
reported as ``None`` ("unavailable") instead of a misleading zero.

The index page renders only a greeting and quick actions; each panel (``WIDGETS``)
is a fragment fetched lazily through :func:`dashboard_widget`, which
:func:`dashboard_urls` mounts on the admin site as ``admin:console_dashboard_widget``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.core.cache import cache
from django.db import DatabaseError
from django.db.models import Count, F, Q, Sum
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, URLPattern, path, reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views.decorators.http import require_GET

from apps.core.app_service import AppService

logger = logging.getLogger(__name__)

CACHE_KEY = "admin:dashboard:metrics:v2"
CACHE_SECONDS = 60
RECENT_ROWS = 5


@dataclass(frozen=True, slots=True)
class Kpi:
    key: str
    label: str
    value: int | None
    hint: str
    icon: str
    url: str
    variant: str
    permission: str


def _model(label: str) -> Any:
    return apps.get_model(label)


def _count(label: str, **filters: Any) -> int | None:
    try:
        return int(_model(label).objects.filter(**filters).count())
    except (LookupError, DatabaseError):
        logger.warning("Dashboard metric unavailable for %s", label, exc_info=True)
        return None


def _storage_health() -> tuple[int | None, int | None]:
    """Return (healthy, total) storage locations."""
    try:
        totals = _model("storage.FirmwareStorageLocation").objects.aggregate(
            total=Count("pk"),
            healthy=Count("pk", filter=Q(status="available")),
        )
    except (LookupError, DatabaseError):
        logger.warning("Dashboard storage metric unavailable", exc_info=True)
        return None, None
    return int(totals["healthy"]), int(totals["total"])


def _recent_signups() -> list[dict[str, Any]]:
    try:
        users = _model("users.CustomUser").objects.order_by("-date_joined")[
            :RECENT_ROWS
        ]
        return [
            {
                "pk": user.pk,
                "label": user.email or user.username,
                "when": user.date_joined,
            }
            for user in users
        ]
    except (LookupError, DatabaseError):
        logger.warning("Dashboard signups unavailable", exc_info=True)
        return []


def _recent_orders() -> list[dict[str, Any]]:
    try:
        orders = _model("shop.Order").objects.order_by("-created_at")[:RECENT_ROWS]
        return [
            {
                "pk": order.pk,
                "label": f"#{order.pk}",
                "status": order.get_status_display(),
                "total": f"{order.total} {order.currency}",
                "when": order.created_at,
            }
            for order in orders
        ]
    except (LookupError, DatabaseError):
        logger.warning("Dashboard orders unavailable", exc_info=True)
        return []


def _recent_pending_firmware() -> list[dict[str, Any]]:
    try:
        rows = (
            _model("firmwares.PendingFirmware")
            .objects.filter(admin_decision="pending")
            .order_by("-created_at")[:RECENT_ROWS]
        )
        return [
            {
                "pk": str(row.pk),
                "label": row.original_file_name,
                "when": row.created_at,
            }
            for row in rows
        ]
    except (LookupError, DatabaseError):
        logger.warning("Dashboard pending firmware unavailable", exc_info=True)
        return []


def _promotion_totals() -> tuple[int | None, int | None]:
    """Return (views, claims) summed over the running promotions."""
    try:
        totals = (
            _model("promotions.Promotion")
            .objects.filter(status="active")
            .aggregate(
                views=Sum(F("page_views") + F("banner_views")),
                claims=Sum("claim_count"),
            )
        )
    except (LookupError, DatabaseError):
        logger.warning("Dashboard promotion metric unavailable", exc_info=True)
        return None, None
    return int(totals["views"] or 0), int(totals["claims"] or 0)


def _stale_posts() -> int | None:
    """Stale live articles, from the blog's public contract (None when unavailable)."""
    counter = AppService.export("blog", "stale_post_count")
    if counter is None:
        return None
    try:
        return int(counter())
    except DatabaseError:
        logger.warning("Dashboard metric unavailable for stale posts", exc_info=True)
        return None


def compute_metrics() -> dict[str, Any]:
    """Collect every dashboard number in one pass (uncached)."""
    week_ago = timezone.now() - timedelta(days=7)
    healthy, total = _storage_health()
    promotion_views, promotion_claims = _promotion_totals()
    return {
        "live_promotions": _count("promotions.Promotion", status="active"),
        "promotion_views": promotion_views,
        "promotion_claims": promotion_claims,
        "pending_firmware": _count(
            "firmwares.PendingFirmware", admin_decision="pending"
        ),
        "pending_comments": _count("comments.Comment", status="pending"),
        "pending_brand_requests": _count(
            "firmwares.BrandCreationRequest", status="pending"
        ),
        "pending_model_requests": _count(
            "firmwares.ModelCreationRequest", status="pending"
        ),
        "pending_variant_requests": _count(
            "firmwares.VariantCreationRequest", status="pending"
        ),
        "pending_chipset_requests": _count(
            "firmwares.ChipsetCreationRequest", status="pending"
        ),
        "open_firmware_requests": _count("firmwares.FirmwareRequest", status="open"),
        "unapproved_tags": _count(
            "tags.Tag", is_curated=False, is_deleted=False, merge_into__isnull=True
        ),
        "open_seo_issues": _count("seo.Issue", status="open"),
        "seo_proposals": _count("seo.ChangeLedger", status="proposed"),
        "storage_healthy": healthy,
        "storage_total": total,
        "failed_seo_jobs": _count("seo.SEOJob", status="failed"),
        "pending_seo_jobs": _count("seo.SEOJob", status="pending"),
        "failed_topics": _count("blog.AutoTopic", status="failed"),
        "queued_topics": _count("blog.AutoTopic", status="queued"),
        "stale_posts": _stale_posts(),
        "pending_listings": _count("marketplace.Listing", status="pending"),
        "pending_sellers": _count("marketplace.SellerVerification", status="pending"),
        "pending_orders": _count("shop.Order", status="pending"),
        "open_bounty_reports": _count("bounty.BountyDispute", status="open"),
        "signups_7d": _count("users.CustomUser", date_joined__gte=week_ago),
        "recent_signups": _recent_signups(),
        "recent_orders": _recent_orders(),
        "recent_pending_firmware": _recent_pending_firmware(),
    }


def get_metrics() -> dict[str, Any]:
    cached = cache.get(CACHE_KEY)
    if isinstance(cached, dict):
        return cached
    metrics = compute_metrics()
    cache.set(CACHE_KEY, metrics, CACHE_SECONDS)
    return metrics


def invalidate_metrics() -> None:
    """Drop the cached dashboard numbers so the next read recounts."""
    cache.delete(CACHE_KEY)


def metric_badge(request: HttpRequest, metric: str) -> str:
    """Sidebar badge text for one cached metric (empty when zero/unavailable)."""
    value = get_metrics().get(metric)
    return str(value) if value else ""


def _changelist(label: str, query: str = "") -> str:
    model = _model(label)
    name = f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist"
    url = reverse(name)
    return f"{url}?{query}" if query else url


def _sum(*values: int | None) -> int | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


CATALOG_QUEUES: tuple[tuple[str, str, str], ...] = (
    (
        "pending_model_requests",
        "firmwares.ModelCreationRequest",
        "status__exact=pending",
    ),
    (
        "pending_brand_requests",
        "firmwares.BrandCreationRequest",
        "status__exact=pending",
    ),
    (
        "pending_variant_requests",
        "firmwares.VariantCreationRequest",
        "status__exact=pending",
    ),
    (
        "pending_chipset_requests",
        "firmwares.ChipsetCreationRequest",
        "status__exact=pending",
    ),
    ("open_firmware_requests", "firmwares.FirmwareRequest", "status__exact=open"),
)


def _catalog_requests_kpi(metrics: dict[str, Any], user: Any | None) -> Kpi:
    """One KPI for the five review queues that opens a queue that has work.

    The total counts only the queues ``user`` may view, and the link goes to the
    first of them that holds pending requests, so the number on the card and the
    list behind it always agree.
    """
    visible: list[tuple[str, str, str, str]] = []
    for metric, label, query in CATALOG_QUEUES:
        model = _model(label)
        permission = f"{model._meta.app_label}.view_{model._meta.model_name}"
        if user is None or user.has_perm(permission):
            visible.append((metric, label, query, permission))
    if not visible:
        visible.append((*CATALOG_QUEUES[0], "firmwares.view_modelcreationrequest"))
    target = next(
        (entry for entry in visible if metrics.get(entry[0])),
        visible[0],
    )
    return Kpi(
        "catalog_requests",
        _("Catalog requests"),
        _sum(*(metrics[entry[0]] for entry in visible)),
        _("Brand, model, variant, chipset and firmware requests to review"),
        "list-plus",
        _changelist(target[1], target[2]),
        "warning",
        target[3],
    )


def build_kpis(metrics: dict[str, Any], user: Any | None = None) -> list[Kpi]:
    healthy = metrics["storage_healthy"]
    total = metrics["storage_total"]
    storage_problems = None if healthy is None or total is None else total - healthy
    return [
        Kpi(
            "pending_firmware",
            _("Pending firmware reviews"),
            metrics["pending_firmware"],
            _("Uploads waiting for a staff decision"),
            "package-open",
            _changelist("firmwares.PendingFirmware", "admin_decision__exact=pending"),
            "warning",
            "firmwares.view_pendingfirmware",
        ),
        Kpi(
            "pending_comments",
            _("Pending comments"),
            metrics["pending_comments"],
            _("Comments held for moderation"),
            "message-square",
            _changelist("comments.Comment", "status__exact=pending"),
            "warning",
            "comments.view_comment",
        ),
        _catalog_requests_kpi(metrics, user),
        Kpi(
            "unapproved_tags",
            _("Unapproved tags"),
            metrics["unapproved_tags"],
            _("Suggested tags that are not curated yet"),
            "tag",
            _changelist("tags.Tag", "is_curated__exact=0"),
            "warning",
            "tags.view_tag",
        ),
        Kpi(
            "open_seo_issues",
            _("Open SEO issues"),
            metrics["open_seo_issues"],
            _("Crawler findings that still need attention"),
            "search-check",
            _changelist("seo.Issue", "status__exact=open"),
            "danger",
            "seo.view_issue",
        ),
        Kpi(
            "seo_proposals",
            _("SEO proposals to approve"),
            metrics["seo_proposals"],
            _("Automation changes waiting in the change ledger"),
            "clipboard-check",
            _changelist("seo.ChangeLedger", "status__exact=proposed"),
            "warning",
            "seo.view_changeledger",
        ),
        Kpi(
            "stale_posts",
            _("Stale articles"),
            metrics.get("stale_posts"),
            _("Published articles that have not been updated for a while"),
            "calendar-clock",
            _changelist("blog.Post", "freshness=stale"),
            "warning",
            "blog.view_post",
        ),
        Kpi(
            "storage",
            _("Storage locations needing attention"),
            storage_problems,
            _("Of %(total)s tracked locations") % {"total": total}
            if total is not None
            else _("Storage status is unavailable"),
            "hard-drive",
            _changelist("storage.FirmwareStorageLocation"),
            "danger" if storage_problems else "success",
            "storage.view_firmwarestoragelocation",
        ),
        Kpi(
            "queue",
            _("Failed background jobs"),
            _sum(metrics["failed_seo_jobs"], metrics["failed_topics"]),
            _("%(pending)s SEO jobs and %(topics)s topics are queued")
            % {
                "pending": metrics["pending_seo_jobs"] or 0,
                "topics": metrics["queued_topics"] or 0,
            },
            "timer",
            _changelist("seo.SEOJob", "status__exact=failed"),
            "danger",
            "seo.view_seojob",
        ),
        Kpi(
            "marketplace",
            _("Marketplace reviews"),
            _sum(metrics["pending_listings"], metrics["pending_sellers"]),
            _("Listings and seller verifications awaiting review"),
            "store",
            _changelist("marketplace.Listing", "status__exact=pending"),
            "warning",
            "marketplace.view_listing",
        ),
        Kpi(
            "orders",
            _("Orders awaiting payment"),
            metrics["pending_orders"],
            _("Offline orders to confirm and provision"),
            "receipt",
            _changelist("shop.Order", "status__exact=pending"),
            "warning",
            "shop.view_order",
        ),
        Kpi(
            "promotions",
            _("Campaigns running"),
            metrics.get("live_promotions"),
            _("%(claims)s claims and %(views)s offer views so far")
            % {
                "claims": metrics.get("promotion_claims") or 0,
                "views": metrics.get("promotion_views") or 0,
            },
            "megaphone",
            _changelist("promotions.Promotion", "status__exact=active"),
            "info",
            "promotions.view_promotion",
        ),
        Kpi(
            "signups",
            _("New accounts (7 days)"),
            metrics["signups_7d"],
            _("Registrations in the last week"),
            "user-plus",
            _changelist("users.CustomUser"),
            "info",
            "users.view_customuser",
        ),
    ]


def visible_kpis(request: HttpRequest, metrics: dict[str, Any]) -> list[Kpi]:
    """The KPIs whose underlying model the user may view."""
    user = request.user
    return [kpi for kpi in build_kpis(metrics, user) if user.has_perm(kpi.permission)]


# -- Lazily loaded dashboard panels ------------------------------------------------
@dataclass(frozen=True, slots=True)
class Widget:
    slug: str
    title: Any  # translatable (lazy) text
    icon: str
    template: str
    build: Callable[[HttpRequest, dict[str, Any]], dict[str, Any]]
    skeleton: str  # which skeleton shape the placeholder shows while loading
    permissions: tuple[str, ...] = ()  # any one grants access; empty = every staff user
    size: str = "full"  # stats (a tile row) | sm | md | lg | full: width in the grid
    refresh: int = (
        0  # seconds between automatic refreshes (paused in a hidden tab); 0 = never
    )

    def allowed(self, request: HttpRequest) -> bool:
        user = request.user
        return not self.permissions or any(user.has_perm(p) for p in self.permissions)


@dataclass(frozen=True, slots=True)
class QuickAction:
    title: str
    icon: str
    url: str


def _safe_reverse(name: str, *args: Any) -> str | None:
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return None


def _quick_actions(request: HttpRequest) -> list[QuickAction]:
    """Shortcuts to everyday jobs, limited to what the user may actually do."""
    user = request.user
    candidates: tuple[tuple[str, str, str | None, str], ...] = (
        (
            _("Write a blog post"),
            "pencil-line",
            _safe_reverse("admin:blog_post_add"),
            "blog.add_post",
        ),
        (
            _("Review firmware uploads"),
            "package-open",
            _changelist_or_none(
                "firmwares.PendingFirmware", "admin_decision__exact=pending"
            ),
            "firmwares.view_pendingfirmware",
        ),
        (
            _("Moderate comments"),
            "message-square",
            _changelist_or_none("comments.Comment", "status__exact=pending"),
            "comments.view_comment",
        ),
        (
            _("Add a brand"),
            "building",
            _safe_reverse("admin:firmwares_brand_add"),
            "firmwares.add_brand",
        ),
        (
            _("Find a user"),
            "user-round",
            _changelist_or_none("users.CustomUser"),
            "users.view_customuser",
        ),
        (
            _("Site settings"),
            "settings",
            _changelist_or_none("site_settings.SiteSettings"),
            "site_settings.view_sitesettings",
        ),
    )
    return [
        QuickAction(title, icon, url)
        for title, icon, url, permission in candidates
        if url and user.has_perm(permission)
    ]


def _changelist_or_none(label: str, query: str = "") -> str | None:
    try:
        return _changelist(label, query)
    except (LookupError, NoReverseMatch):
        return None


def _overview(request: HttpRequest, metrics: dict[str, Any]) -> dict[str, Any]:
    """Headline numbers: what is waiting, what is failing, and how the site is growing."""
    kpis = visible_kpis(request, metrics)
    waiting = [k for k in kpis if k.variant == "warning" and k.value]
    failing = [k for k in kpis if k.variant == "danger" and k.value]
    by_key = {kpi.key: kpi for kpi in kpis}
    return {
        "waiting_total": sum(int(k.value or 0) for k in waiting),
        "waiting_queues": len(waiting),
        "failing_total": sum(int(k.value or 0) for k in failing),
        "failing_checks": len(failing),
        "signups": by_key.get("signups"),
        "promotions": by_key.get("promotions"),
    }


_VARIANT_ORDER = {"danger": 0, "warning": 1, "info": 2, "success": 3}


def _attention(request: HttpRequest, metrics: dict[str, Any]) -> dict[str, Any]:
    """Queues that hold work, most urgent first, plus counters that could not be read."""
    kpis = visible_kpis(request, metrics)
    actionable = [k for k in kpis if k.variant in {"warning", "danger"} and k.value]
    actionable.sort(key=lambda k: (_VARIANT_ORDER[k.variant], -int(k.value or 0)))
    return {
        "queues": actionable,
        "total": sum(int(k.value or 0) for k in actionable),
        "unavailable": [k for k in kpis if k.value is None],
        "has_queues": bool(kpis),
    }


def _health(request: HttpRequest, metrics: dict[str, Any]) -> dict[str, Any]:
    """Operational checks: storage, background jobs and crawler findings."""
    wanted = ("storage", "queue", "open_seo_issues", "stale_posts")
    by_key = {kpi.key: kpi for kpi in visible_kpis(request, metrics)}
    return {"checks": [by_key[key] for key in wanted if key in by_key]}


def _activity(request: HttpRequest, metrics: dict[str, Any]) -> dict[str, Any]:
    """Newest sign-ups, orders and firmware uploads merged into one timeline."""
    user = request.user
    entries: list[dict[str, Any]] = []
    if user.has_perm("firmwares.view_pendingfirmware"):
        for row in metrics["recent_pending_firmware"]:
            url = _safe_reverse("admin:firmwares_pendingfirmware_change", row["pk"])
            entries.append(
                {
                    "icon": "package-open",
                    "title": row["label"],
                    "meta": _("Firmware waiting for review"),
                    "url": url,
                    "when": row["when"],
                }
            )
    if user.has_perm("users.view_customuser"):
        for row in metrics["recent_signups"]:
            entries.append(
                {
                    "icon": "user-plus",
                    "title": row["label"],
                    "meta": _("New account"),
                    "url": _safe_reverse("admin:users_customuser_change", row["pk"]),
                    "when": row["when"],
                }
            )
    if user.has_perm("shop.view_order"):
        for row in metrics["recent_orders"]:
            entries.append(
                {
                    "icon": "receipt",
                    "title": f"{row['label']} · {row['total']}",
                    "meta": _("Order · %(status)s") % {"status": row["status"]},
                    "url": _safe_reverse("admin:shop_order_change", row["pk"]),
                    "when": row["when"],
                }
            )
    entries.sort(key=lambda entry: entry["when"], reverse=True)
    return {"entries": entries[: RECENT_ROWS * 2]}


WIDGETS: tuple[Widget, ...] = (
    Widget(
        "overview",
        gettext_lazy("At a glance"),
        "gauge",
        "console/dashboard/overview.html",
        _overview,
        "tiles",
        size="stats",
        refresh=60,
    ),
    Widget(
        "attention",
        gettext_lazy("Needs your attention"),
        "list-checks",
        "console/dashboard/attention.html",
        _attention,
        "list",
        size="lg",
        refresh=60,
    ),
    Widget(
        "activity",
        gettext_lazy("Recent activity"),
        "activity",
        "console/dashboard/activity.html",
        _activity,
        "list",
        ("firmwares.view_pendingfirmware", "users.view_customuser", "shop.view_order"),
        refresh=120,
    ),
    Widget(
        "health",
        gettext_lazy("System health"),
        "heart-pulse",
        "console/dashboard/health.html",
        _health,
        "list",
        size="sm",
        refresh=60,
    ),
)
WIDGET_BY_SLUG = {widget.slug: widget for widget in WIDGETS}


def _greeting(now_hour: int) -> str:
    if now_hour < 5:
        return _("Working late")
    if now_hour < 12:
        return _("Good morning")
    if now_hour < 18:
        return _("Good afternoon")
    return _("Good evening")


def dashboard_page_context(request: HttpRequest) -> dict[str, Any]:
    """Everything the dashboard index needs without touching the metrics cache."""
    now = timezone.localtime()
    user = request.user
    full_name = user.get_full_name() if user.is_authenticated else ""
    name = ((full_name or user.get_username()).split() or ["there"])[0]
    return {
        "greeting": _greeting(now.hour),
        "display_name": name,
        "today": now,
        "quick_actions": _quick_actions(request),
        "widgets": [
            {
                "slug": widget.slug,
                "title": widget.title,
                "icon": widget.icon,
                "skeleton": widget.skeleton,
                "size": widget.size,
                "refresh": widget.refresh,
                "url": reverse("admin:console_dashboard_widget", args=[widget.slug]),
            }
            for widget in WIDGETS
            if widget.allowed(request)
        ],
    }


def dashboard_callback(request: HttpRequest, context: dict[str, Any]) -> dict[str, Any]:
    """Merge the dashboard page data into the admin index context (see ``MobofirmAdminSite``)."""
    return {**context, **dashboard_page_context(request)}


@require_GET
def dashboard_widget(request: HttpRequest, slug: str) -> HttpResponse:
    """Render one dashboard panel; a direct visit gets it inside the full shell."""
    widget = WIDGET_BY_SLUG.get(slug)
    if widget is None or not widget.allowed(request):
        raise Http404(slug)
    context = {
        "widget": widget,
        "title": widget.title,
        **widget.build(request, get_metrics()),
    }
    is_fragment = request.headers.get("HX-Request") == "true"
    template = widget.template if is_fragment else "console/dashboard/widget_page.html"
    return render(request, template, {**context, "widget_template": widget.template})


def dashboard_urls(admin_site: Any) -> list[URLPattern]:
    """URL patterns to prepend to the admin site's own (see ``MobofirmAdminSite``)."""
    return [
        path(
            "console/widgets/<slug:slug>/",
            admin_site.admin_view(dashboard_widget),
            name="console_dashboard_widget",
        )
    ]
