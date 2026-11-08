"""Consent state, decisions, policy publishing and retention.

Public contract for the rest of the project::

    from apps.consent.services import has_consent
    has_consent(request, "analytics")   # -> bool

The visitor's choice lives in one first-party cookie (policy version, allowed
optional categories, random subject id). Every choice is also written to an
append-only, anonymised ``ConsentDecision`` for audit. Optional categories are
opt-in: with no valid choice, or after the policy version changes, they are off.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from weakref import WeakKeyDictionary

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.encoding import force_bytes

from apps.consent.constants import (
    ACTION_ACCEPT_ALL,
    ACTION_CUSTOM,
    ACTION_REJECT_ALL,
    BUILTIN_CATEGORIES,
    NECESSARY,
    SOURCE_PRIVACY_CENTRE,
    TRACKING_COOKIE_PREFIXES,
)
from apps.consent.models import ConsentCategory, ConsentDecision, ConsentPolicy
from apps.core.app_service import app_enabled
from apps.core.utils.ip import get_client_ip

logger = logging.getLogger(__name__)

POLICY_CACHE_KEY = "consent:active-policy:v1"
POLICY_CACHE_SECONDS = 300
DEFAULT_BANNER_TEXT = (
    "We use a small first-party cookie to remember your choice. Optional analytics "
    "and advertising cookies stay off unless you allow them."
)
DEFAULT_DETAILS_TEXT = (
    "Choose which optional cookies and trackers this site may use. Strictly "
    "necessary cookies are always on. You can change or withdraw your choice at "
    "any time from this page or from Cookie settings in the footer."
)
_COOKIE_RE = re.compile(r"^(\d{1,9}):([a-z]*(?:\+[a-z]+)*):([0-9a-f]{32})$")


@dataclass(frozen=True, slots=True)
class CategoryView:
    slug: str
    name: str
    description: str
    required: bool


@dataclass(frozen=True, slots=True)
class PolicyView:
    """Plain, cacheable copy of the active policy."""

    version: int
    title: str
    banner_text: str
    details_text: str
    privacy_url: str
    categories: tuple[CategoryView, ...]

    @property
    def optional(self) -> tuple[CategoryView, ...]:
        return tuple(c for c in self.categories if not c.required)


@dataclass(frozen=True, slots=True)
class ConsentState:
    """What the visitor has decided under the currently active policy."""

    decided: bool
    granted: frozenset[str]
    subject: uuid.UUID | None
    enabled: bool = True

    def allows(self, category: str) -> bool:
        if category == NECESSARY:
            return True
        return self.enabled and category in self.granted


_STATE_BY_REQUEST: WeakKeyDictionary[HttpRequest, ConsentState] = WeakKeyDictionary()


# ---------------------------------------------------------------- policy ----


def ensure_defaults() -> ConsentPolicy:
    """Create the built-in categories and a first published policy when missing.

    Idempotent; safe to call from ``post_migrate`` and lazily.
    """
    with transaction.atomic():
        for slug, (name, description, required, order) in BUILTIN_CATEGORIES.items():
            ConsentCategory.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "description": description,
                    "is_required": required,
                    "sort_order": order,
                },
            )
        active = ConsentPolicy.objects.filter(is_active=True).first()
        if active is not None:
            return active
        policy = ConsentPolicy.objects.order_by("-version").first()
        if policy is None:
            policy = ConsentPolicy.objects.create(
                version=1,
                banner_text=DEFAULT_BANNER_TEXT,
                details_text=DEFAULT_DETAILS_TEXT,
            )
        return publish_policy(policy)


def _snapshot_categories() -> list[dict[str, Any]]:
    return [
        {
            "slug": c.slug,
            "name": c.name,
            "description": c.description,
            "required": c.is_required,
        }
        for c in ConsentCategory.objects.filter(is_active=True)
    ]


@transaction.atomic
def publish_policy(policy: ConsentPolicy) -> ConsentPolicy:
    """Make ``policy`` the one active version; every visitor is asked again."""
    ConsentPolicy.objects.filter(is_active=True).exclude(pk=policy.pk).update(
        is_active=False
    )
    policy.categories_snapshot = _snapshot_categories()
    policy.published_at = policy.published_at or timezone.now()
    policy.is_active = True
    policy.save(update_fields=["categories_snapshot", "published_at", "is_active"])
    invalidate_policy_cache()
    return policy


def next_version() -> int:
    latest = ConsentPolicy.objects.aggregate(top=Max("version"))["top"]
    return (latest or 0) + 1


def invalidate_policy_cache() -> None:
    cache.delete(POLICY_CACHE_KEY)


def _view_of(policy: ConsentPolicy) -> PolicyView:
    categories = tuple(
        CategoryView(
            slug=str(item["slug"]),
            name=str(item["name"]),
            description=str(item["description"]),
            required=bool(item["required"]),
        )
        for item in policy.categories_snapshot
    )
    return PolicyView(
        version=policy.version,
        title=policy.title,
        banner_text=policy.banner_text,
        details_text=policy.details_text,
        privacy_url=policy.privacy_url,
        categories=categories,
    )


def active_policy() -> PolicyView:
    """The published policy, cached; created on first use if none exists."""
    cached = cache.get(POLICY_CACHE_KEY)
    if isinstance(cached, PolicyView):
        return cached
    policy = ConsentPolicy.objects.filter(is_active=True).first() or ensure_defaults()
    view = _view_of(policy)
    cache.set(POLICY_CACHE_KEY, view, POLICY_CACHE_SECONDS)
    return view


# ---------------------------------------------------------------- cookie ----


def cookie_name() -> str:
    return str(settings.CONSENT_COOKIE_NAME)


def encode_cookie(version: int, granted: Iterable[str], subject: uuid.UUID) -> str:
    return f"{version}:{'+'.join(sorted(set(granted)))}:{subject.hex}"


def parse_cookie(raw: str | None) -> tuple[int, frozenset[str], uuid.UUID] | None:
    """Strictly parse the cookie; anything unexpected counts as no decision."""
    match = _COOKIE_RE.match(raw or "")
    if match is None:
        return None
    version, categories, subject = match.groups()
    granted = frozenset(filter(None, categories.split("+")))
    return int(version), granted, uuid.UUID(subject)


def read_state(request: HttpRequest) -> ConsentState:
    """The visitor's choice for the active policy (memoised per request)."""
    cached = _STATE_BY_REQUEST.get(request)
    if cached is not None:
        return cached
    enabled = app_enabled("consent")
    parsed = parse_cookie(request.COOKIES.get(cookie_name()))
    if parsed is None:
        state = ConsentState(False, frozenset(), None, enabled)
    else:
        version, granted, subject = parsed
        if version == active_policy().version:
            state = ConsentState(True, granted, subject, enabled)
        else:
            state = ConsentState(False, frozenset(), subject, enabled)
    _STATE_BY_REQUEST[request] = state
    return state


def has_consent(request: HttpRequest, category: str) -> bool:
    """Whether the visitor allows ``category`` (necessary/analytics/advertising).

    Strictly necessary is always true. Everything else is opt-in: false without a
    valid decision for the current policy version, and while the consent app is
    switched off in the application registry.
    """
    return read_state(request).allows(category)


def needs_decision(request: HttpRequest) -> bool:
    state = read_state(request)
    return state.enabled and not state.decided


def _set_cookie(response: HttpResponse, value: str) -> None:
    response.set_cookie(
        cookie_name(),
        value,
        max_age=settings.CONSENT_COOKIE_MAX_AGE,
        secure=bool(settings.SESSION_COOKIE_SECURE),
        httponly=True,
        samesite="Lax",
        path="/",
    )


def expire_tracking_cookies(request: HttpRequest, response: HttpResponse) -> None:
    """Remove analytics cookies already set in this browser (withdrawal)."""
    for name in request.COOKIES:
        if name.startswith(TRACKING_COOKIE_PREFIXES):
            response.delete_cookie(name, path="/")


# -------------------------------------------------------------- decisions ----


def hash_identifier(value: str) -> str:
    """Keyed hash so audit records never hold a raw IP address or user agent."""
    if not value:
        return ""
    return hmac.new(
        force_bytes(settings.SECRET_KEY), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def resolve_granted(
    policy: PolicyView, action: str, selected: Iterable[str] = ()
) -> frozenset[str]:
    offered = {c.slug for c in policy.optional}
    if action == ACTION_ACCEPT_ALL:
        return frozenset(offered)
    if action == ACTION_REJECT_ALL:
        return frozenset()
    if action == ACTION_CUSTOM:
        return frozenset(offered.intersection(selected))
    raise ValueError(f"Unknown consent action: {action}")


def record_decision(
    request: HttpRequest,
    response: HttpResponse,
    *,
    action: str,
    selected: Iterable[str] = (),
    source: str = SOURCE_PRIVACY_CENTRE,
) -> ConsentState:
    """Store the choice (cookie + audit record) and expire cookies it revokes."""
    view = active_policy()
    granted = resolve_granted(view, action, selected)
    previous = parse_cookie(request.COOKIES.get(cookie_name()))
    subject = previous[2] if previous else uuid.uuid4()
    revoked = bool(previous and previous[1] - granted)
    policy = ConsentPolicy.objects.get(version=view.version)
    latest = ConsentDecision.objects.filter(subject=subject).first()
    if (
        latest is None
        or latest.policy_id != policy.pk
        or set(latest.granted) != set(granted)
    ):
        ConsentDecision.objects.create(
            subject=subject,
            policy=policy,
            granted=sorted(granted),
            action=action,
            source=source,
            ip_hash=hash_identifier(get_client_ip(request)),
            user_agent_hash=hash_identifier(request.META.get("HTTP_USER_AGENT", "")),
        )
    _set_cookie(response, encode_cookie(view.version, granted, subject))
    if revoked or action == ACTION_REJECT_ALL:
        expire_tracking_cookies(request, response)
    state = ConsentState(True, granted, subject, app_enabled("consent"))
    _STATE_BY_REQUEST[request] = state
    return state


# -------------------------------------------------------- stats & retention ----


def consent_stats() -> dict[str, Any]:
    """Latest decision per visitor under the active policy, counted by category."""
    policy = ConsentPolicy.objects.filter(is_active=True).first()
    if policy is None:
        return {"policy": None, "visitors": 0, "by_category": [], "last_30_days": {}}
    latest_ids = (
        ConsentDecision.objects.filter(policy=policy)
        .values("subject")
        .annotate(last=Max("id"))
        .values("last")
    )
    granted_sets = ConsentDecision.objects.filter(id__in=latest_ids).values_list(
        "granted", flat=True
    )
    visitors = 0
    counts: Counter[str] = Counter()
    for granted in granted_sets.iterator(chunk_size=2000):
        visitors += 1
        counts.update(granted)
    by_category = [
        {
            "slug": c.slug,
            "name": c.name,
            "granted": counts.get(c.slug, 0),
            "percent": round(100 * counts.get(c.slug, 0) / visitors) if visitors else 0,
        }
        for c in _view_of(policy).optional
    ]
    since = timezone.now() - timedelta(days=30)
    recent = Counter(
        ConsentDecision.objects.filter(created_at__gte=since).values_list(
            "action", flat=True
        )
    )
    return {
        "policy": policy,
        "visitors": visitors,
        "by_category": by_category,
        "last_30_days": dict(recent),
    }


def purge_old_records(days: int | None = None) -> int:
    """Delete audit records older than the retention window; returns the count."""
    keep_days = days if days is not None else settings.CONSENT_RECORD_RETENTION_DAYS
    if keep_days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=keep_days)
    deleted, _ = ConsentDecision.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def create_draft_policy(
    *, banner_text: str = "", details_text: str = "", title: str = ""
) -> ConsentPolicy:
    """A new unpublished version seeded from the active one."""
    base = ConsentPolicy.objects.filter(is_active=True).first()
    for _attempt in range(3):
        try:
            with transaction.atomic():
                return ConsentPolicy.objects.create(
                    version=next_version(),
                    title=title or (base.title if base else "Consent policy"),
                    banner_text=banner_text
                    or (base.banner_text if base else DEFAULT_BANNER_TEXT),
                    details_text=details_text
                    or (base.details_text if base else DEFAULT_DETAILS_TEXT),
                    privacy_url=base.privacy_url if base else "/pages/privacy/",
                )
        except IntegrityError:
            logger.info("Consent policy version clash; retrying")
    raise RuntimeError("Could not allocate a consent policy version.")


__all__ = [
    "ConsentState",
    "PolicyView",
    "active_policy",
    "consent_stats",
    "create_draft_policy",
    "ensure_defaults",
    "has_consent",
    "needs_decision",
    "publish_policy",
    "purge_old_records",
    "read_state",
    "record_decision",
]
