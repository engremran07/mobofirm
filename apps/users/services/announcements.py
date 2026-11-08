"""Site banners built from live announcements.

An announcement is a banner while it is active, marked global, started and not
expired, and addressed to the visitor's audience (``all``, signed-in ``user``,
``staff``). Apps that own an announcement (a promotion publishes one while it runs)
narrow who sees it with :func:`register_announcement_gate` and learn when a signed-in
visitor first sees it with :func:`register_announcement_view_hook`; this module never
imports them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from apps.users.models import Announcement

DISMISSED_COOKIE = "mf_dismissed_announcements"
DISMISSED_MAX_AGE = 60 * 60 * 24 * 30
MAX_DISMISSED = 20
MAX_BANNERS = 2
SEEN_SESSION_KEY = "announcements_seen"

AnnouncementGate = Callable[[Any, Sequence[Announcement]], Sequence[Announcement]]
AnnouncementViewHook = Callable[[HttpRequest, Sequence[Announcement]], None]

_gates: list[AnnouncementGate] = []
_view_hooks: list[AnnouncementViewHook] = []


def register_announcement_gate(gate: AnnouncementGate) -> AnnouncementGate:
    """Let an owner app narrow ``(user, announcements)`` to those the user may see."""
    if gate not in _gates:
        _gates.append(gate)
    return gate


def register_announcement_view_hook(hook: AnnouncementViewHook) -> AnnouncementViewHook:
    """Call ``hook(request, announcements)`` once per session per new banner."""
    if hook not in _view_hooks:
        _view_hooks.append(hook)
    return hook


def dismissed_ids(request: HttpRequest) -> set[int]:
    raw = request.COOKIES.get(DISMISSED_COOKIE, "")
    return {int(part) for part in raw.split(",") if part.isdigit()}


def remember_dismissal(request: HttpRequest, response: HttpResponse, pk: int) -> None:
    ids = sorted(dismissed_ids(request) | {pk})[-MAX_DISMISSED:]
    response.set_cookie(
        DISMISSED_COOKIE,
        ",".join(str(value) for value in ids),
        max_age=DISMISSED_MAX_AGE,
        samesite="Lax",
        secure=request.is_secure(),
    )


def _audiences(user: Any) -> list[str]:
    if user is None or not getattr(user, "is_authenticated", False):
        return ["all"]
    audiences = ["all", "user"]
    if getattr(user, "is_staff", False):
        audiences.append("staff")
    return audiences


def banner_announcements(request: HttpRequest) -> list[Announcement]:
    """The banners to show this visitor right now (at most :data:`MAX_BANNERS`)."""
    user = getattr(request, "user", None)
    now = timezone.now()
    candidates = list(
        Announcement.objects.filter(
            is_active=True,
            is_global=True,
            start_at__lte=now,
            audience__in=_audiences(user),
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .exclude(pk__in=dismissed_ids(request))
        .order_by("-start_at")[: MAX_BANNERS * 3]
    )
    for gate in _gates:
        candidates = list(gate(user, candidates))
    banners = candidates[:MAX_BANNERS]
    _record_views(request, banners)
    return banners


def _record_views(request: HttpRequest, banners: Sequence[Announcement]) -> None:
    """Report each banner once per signed-in session; anonymous visits stay stateless."""
    user = getattr(request, "user", None)
    if not _view_hooks or not banners:
        return
    if user is None or not user.is_authenticated:
        return
    session = getattr(request, "session", None)
    if session is None:
        return
    seen = set(session.get(SEEN_SESSION_KEY, []))
    fresh = [banner for banner in banners if banner.pk not in seen]
    if not fresh:
        return
    session[SEEN_SESSION_KEY] = sorted(seen | {banner.pk for banner in fresh})
    for hook in _view_hooks:
        hook(request, fresh)
