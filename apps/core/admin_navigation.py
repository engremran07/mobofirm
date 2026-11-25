"""Permission-aware sidebar, status tabs and account links of the staff console.

The catalogue lives in :mod:`apps.core.admin_nav_catalog`; this module resolves it
per request: an entry appears only when its model is registered in the admin and
the user may view or change it (or, for staff tools outside the admin, holds the
stated permission). Registered models that no group lists are appended to an
"Other" group so nothing is ever unreachable;
``tests/backend/admin/test_admin_all_models.py`` fails if that group is non-empty.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from django.apps import apps as django_apps
from django.contrib import admin
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.core.admin_dashboard import metric_badge
from apps.core.admin_nav_catalog import (
    NAV_GROUPS,
    OTHER_GROUP_ICON,
    OTHER_ITEM_ICON,
    OVERVIEW_ICON,
    ModelEntry,
    NavGroup,
    UrlEntry,
)
from apps.core.app_service import app_enabled

__all__ = [
    "BADGE_VARIANTS",
    "NAV_GROUPS",
    "ModelEntry",
    "NavGroup",
    "UrlEntry",
    "account_links",
    "build_sidebar",
    "build_tabs",
    "changelist_url",
    "listed_model_labels",
    "palette_items",
    "tabs_for_path",
    "unlisted_models",
]

BADGE_VARIANTS = {"danger", "warning", "info", "success", "primary"}

NavItem = dict[str, Any]


def changelist_url(model_label: str) -> str | None:
    """Admin changelist URL for ``app_label.modelname`` or None if unregistered."""
    app_label, _sep, model_name = model_label.partition(".")
    try:
        return reverse(f"admin:{app_label}_{model_name}_changelist")
    except NoReverseMatch:
        return None


def _registered_models() -> dict[str, Any]:
    return {
        model._meta.label_lower: admin.site.get_model_admin(model)
        for model in django_apps.get_models()
        if admin.site.is_registered(model)
    }


def listed_model_labels() -> set[str]:
    return {
        entry.label
        for group in NAV_GROUPS
        for entry in group.entries
        if isinstance(entry, ModelEntry)
    }


def unlisted_models() -> list[str]:
    """Registered model labels missing from every navigation group."""
    return sorted(set(_registered_models()) - listed_model_labels())


def _model_item(
    request: HttpRequest, entry: ModelEntry, registry: dict[str, Any]
) -> NavItem | None:
    model_admin = registry.get(entry.label)
    if model_admin is None or not model_admin.has_view_or_change_permission(request):
        return None
    url = changelist_url(entry.label)
    if url is None:
        return None
    title = (
        _(entry.title)
        if entry.title
        else str(model_admin.opts.verbose_name_plural).capitalize()
    )
    item: NavItem = {"title": title, "icon": entry.icon, "link": url, "badge": ""}
    if entry.badge:
        item["badge"] = metric_badge(request, entry.badge)
        item["badge_variant"] = entry.badge_variant
    return item


def _url_item(request: HttpRequest, entry: UrlEntry) -> NavItem | None:
    user = request.user
    if not user.is_active or not user.is_staff:
        return None
    if entry.permission and not user.has_perm(entry.permission):
        return None
    try:
        url = reverse(entry.url_name)
    except NoReverseMatch:
        return None
    return {"title": _(entry.title), "icon": entry.icon, "link": url, "badge": ""}


def _group_badge(items: Sequence[NavItem]) -> str:
    """Total of the item badges, so a collapsed group still signals waiting work."""
    total = sum(int(item["badge"]) for item in items if item.get("badge"))
    return str(total) if total else ""


def _group(
    title: str, icon: str, items: list[NavItem], *, collapsible: bool = True
) -> dict[str, Any]:
    return {
        "key": slugify(title) or "group",
        "title": title,
        "icon": icon,
        "items": items,
        "collapsible": collapsible,
        "badge": _group_badge(items),
        "active": False,
        "open": not collapsible,
    }


def build_sidebar(request: HttpRequest) -> list[dict[str, Any]]:
    """Resolved sidebar groups for ``request`` with exactly one active item."""
    registry = _registered_models()
    groups = [
        _group(
            _("Overview"),
            OVERVIEW_ICON,
            [
                {
                    "title": _("Dashboard"),
                    "icon": OVERVIEW_ICON,
                    "link": reverse("admin:index"),
                    "badge": "",
                }
            ],
            collapsible=False,
        )
    ]
    for nav_group in NAV_GROUPS:
        items = [
            item
            for entry in nav_group.entries
            if (
                item := (
                    _model_item(request, entry, registry)
                    if isinstance(entry, ModelEntry)
                    else _url_item(request, entry)
                )
            )
        ]
        if items:
            groups.append(
                _group(
                    _(nav_group.title),
                    nav_group.icon,
                    items,
                    collapsible=nav_group.collapsible,
                )
            )
    other = _other_group(request, registry)
    if other:
        groups.append(other)
    _mark_active_item(request, groups)
    return groups


def _other_group(
    request: HttpRequest, registry: dict[str, Any]
) -> dict[str, Any] | None:
    by_app: dict[str, list[NavItem]] = defaultdict(list)
    for label in unlisted_models():
        item = _model_item(request, ModelEntry(label, OTHER_ITEM_ICON), registry)
        if item is not None:
            by_app[label.partition(".")[0]].append(item)
    if not by_app:
        return None
    items = [item for app_label in sorted(by_app) for item in by_app[app_label]]
    return _group(_("Other"), OTHER_GROUP_ICON, items)


def _link_path(link: str) -> str:
    return link.partition("?")[0]


def _mark_active_item(request: HttpRequest, groups: list[dict[str, Any]]) -> None:
    """Highlight exactly one entry: the one with the longest matching path.

    A prefix test alone would also light a parent entry (a model changelist) when
    the page is a child URL (that model's dashboard), so two rows looked selected.
    Each group learns whether it holds the active entry and opens when it does.
    """
    path = request.path
    index_path = reverse("admin:index")
    best: NavItem | None = None
    best_length = -1
    for group in groups:
        for item in group["items"]:
            item_path = _link_path(str(item["link"]))
            if item_path == index_path:
                matches = path == index_path
            else:
                matches = path == item_path or path.startswith(
                    item_path.rstrip("/") + "/"
                )
            if matches and len(item_path) > best_length:
                best, best_length = item, len(item_path)
    for group in groups:
        for item in group["items"]:
            item["active"] = item is best
        group["active"] = any(item["active"] for item in group["items"])
        group["open"] = group["open"] or group["active"]


def palette_items(groups: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Flat, JSON-ready list of every reachable destination for the command palette."""
    return [
        {
            "title": str(item["title"]),
            "group": str(group["title"]),
            "icon": str(item["icon"]),
            "url": str(item["link"]),
        }
        for group in groups
        for item in group["items"]
    ]


# -- Status tabs ----------------------------------------------------------------
TabItem = tuple[str, str, str]  # (title, model label, query string)

_FIRMWARE_TYPES: tuple[tuple[str, str], ...] = (
    ("Pending review", "firmwares.pendingfirmware"),
    ("Official", "firmwares.officialfirmware"),
    ("Service / Engineering", "firmwares.engineeringfirmware"),
    ("Readback", "firmwares.readbackfirmware"),
    ("Modified", "firmwares.modifiedfirmware"),
    ("Other", "firmwares.otherfirmware"),
    ("Unclassified", "firmwares.unclassifiedfirmware"),
)

_PENDING_APPROVED_REJECTED = (
    ("Pending", "pending"),
    ("Approved", "approved"),
    ("Rejected", "rejected"),
)

# (model label, status field, statuses) -> an "All" tab plus one tab per status.
_STATUS_TABS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("firmwares.pendingfirmware", "admin_decision", _PENDING_APPROVED_REJECTED),
    (
        "comments.comment",
        "status",
        (*_PENDING_APPROVED_REJECTED, ("Spam", "spam")),
    ),
    (
        "blog.post",
        "status",
        (
            ("Drafts", "draft"),
            ("In review", "in_review"),
            ("Scheduled", "scheduled"),
            ("Published", "published"),
            ("Archived", "archived"),
        ),
    ),
    (
        "seo.issue",
        "status",
        (("Open", "open"), ("Resolved", "resolved"), ("Ignored", "ignored")),
    ),
    (
        "seo.changeledger",
        "status",
        (
            ("Proposed", "proposed"),
            ("Applied", "applied"),
            ("Rejected", "rejected"),
            ("Rolled back", "rolled_back"),
        ),
    ),
    (
        "shop.order",
        "status",
        (
            ("Pending", "pending"),
            ("Paid", "paid"),
            ("Refunded", "refunded"),
            ("Cancelled", "cancelled"),
        ),
    ),
    ("marketplace.listing", "status", _PENDING_APPROVED_REJECTED),
    ("marketplace.sellerverification", "status", _PENDING_APPROVED_REJECTED),
)

_TAG_TABS: tuple[TabItem, ...] = (
    ("All", "tags.tag", ""),
    ("Unapproved", "tags.tag", "is_curated__exact=0"),
    ("Curated", "tags.tag", "is_curated__exact=1"),
    ("Suggested tags", "tags.tag", "is_suggested__exact=1"),
)


def _tab(
    request: HttpRequest, models: list[str], items: Sequence[TabItem]
) -> dict[str, Any] | None:
    """One tab group; hidden unless at least two of its tabs are reachable."""
    registry = _registered_models()
    tab_items = []
    for title, label, query in items:
        model_admin = registry.get(label)
        url = changelist_url(label)
        if (
            model_admin is None
            or url is None
            or not model_admin.has_view_or_change_permission(request)
        ):
            continue
        tab_items.append(
            {"title": _(title), "link": f"{url}?{query}" if query else url}
        )
    if len(tab_items) < 2:
        return None
    return {
        "models": [label for label in models if label in registry],
        "items": tab_items,
    }


def build_tabs(request: HttpRequest) -> list[dict[str, Any]]:
    """Every tab group the user can see; each lists the models that show it."""
    tabs = [
        _tab(
            request,
            [label for _title, label in _FIRMWARE_TYPES],
            [(title, label, "") for title, label in _FIRMWARE_TYPES],
        ),
        *(
            _tab(
                request,
                [label],
                [
                    ("All", label, ""),
                    *(
                        (title, label, f"{field}__exact={value}")
                        for title, value in statuses
                    ),
                ],
            )
            for label, field, statuses in _STATUS_TABS
        ),
        _tab(request, ["tags.tag"], _TAG_TABS),
    ]
    return [tab for tab in tabs if tab is not None]


def tabs_for_path(request: HttpRequest, model_label: str) -> list[dict[str, Any]]:
    """Tab links for the page of ``model_label`` with the current one marked active.

    A link is active when its path matches and every query pair it carries is in the
    request; the bare "All" link is active only when no tab filter is applied.
    """
    for tab in build_tabs(request):
        if model_label not in tab["models"]:
            continue
        links = []
        for item in tab["items"]:
            path, _sep, query = str(item["link"]).partition("?")
            pairs = [pair for pair in query.split("&") if pair]
            if pairs:
                active = request.path == path and all(
                    request.GET.get(key) == value
                    for key, _eq, value in (pair.partition("=") for pair in pairs)
                )
            else:
                active = request.path == path and not any(
                    key.endswith("__exact") for key in request.GET
                )
            links.append({**item, "active": active})
        return links
    return []


def account_links(request: HttpRequest) -> list[dict[str, str]]:
    entries = [
        (_("My dashboard"), "layout-dashboard", "users:dashboard"),
        (_("My profile"), "user-round", "users:profile"),
        (_("My contributions"), "file-text", "contributions:hub"),
        (_("Notifications"), "bell", "users:notifications"),
    ]
    if app_enabled("wallet"):
        entries.insert(2, (_("My wallet"), "wallet", "wallet:dashboard"))

    links = []
    for title, icon, url_name in entries:
        try:
            links.append({"title": title, "icon": icon, "link": reverse(url_name)})
        except NoReverseMatch:
            continue
    return links
