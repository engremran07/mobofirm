"""
apps.users.services.erasure
---------------------------
Account erasure: anonymise an account in place (audit F-041).

``users`` owns the orchestration; every other app owns its own data and its own
anonymiser. Nothing here imports another domain app.

Why in place
    The account row is kept as a tombstone because ledgers, orders and audit trails
    reference its primary key and must keep reconciling. Everything that identifies
    the person is scrubbed or deleted; the row can no longer sign in.

Contract for other apps (also summarised in the cross-app request log)
    An app that holds personal data exports from its ``api.py`` (resolved through
    ``AppService``, never imported); ``apps.wallet.api`` already does::

        def erasure_blockers(user) -> Sequence[str]      # optional
        def anonymise_user(user) -> Mapping[str, int] | <dataclass of ints> | None

    * ``erasure_blockers`` lists obligations that still need the personal data (an
      open payout, credits in escrow). Any blocker makes the erasure wait (see below).
    * ``anonymise_user`` runs inside the caller's single transaction: do not commit,
      and defer side effects (file deletion, network calls) with
      ``transaction.on_commit``.
    * It is idempotent: calling it for an account that has nothing left must succeed
      and report zeros. It keeps every amount and ledger row that must reconcile.
    * It reports counts only (never personal data); the report is stored in the
      receipt of the request.
    * Failing means raising: the whole erasure rolls back and is retried (bounded).

    An installed app that is expected to hold personal data
    (``EXPECTED_HANDLER_APPS``) but exports no handler is recorded as a *gap* in the
    receipt and the request ends as ``partial`` instead of ``completed``; a later
    request completes the remainder (the users part is a no-op by then).

Lifecycle
    Self-service: request (password + verified email, no blockers) -> confirmation
    email with a one-time link -> confirmed = ``scheduled`` for now + grace period ->
    the periodic task executes it; the account holder can cancel until then.
    Staff: ``anonymize_account`` runs immediately for an operator holding
    ``users.anonymize_account``; a privileged (staff/superuser) account must be
    demoted first and an account with blockers is refused with the reasons.

Transaction and failure model
    * claim: one short transaction locks the request and counts the attempt (so a
      crash cannot cause unbounded retries);
    * work: one transaction locks the account and the request, checks the blockers,
      scrubs the users data, calls every app handler and records the receipt. Any
      failure rolls the whole thing back, leaving the account exactly as it was;
    * blockers that appear after confirmation make the request wait: it stays
      ``scheduled``, is re-checked daily without using an attempt and lists the
      blocking apps in its receipt; after ``ACCOUNT_ERASURE_BLOCKED_MAX_DAYS``
      (default 30) since confirmation it is terminally ``failed`` for staff;
    * after ``ACCOUNT_ERASURE_MAX_ATTEMPTS`` (default 3) failed attempts the request
      is terminally ``failed`` and needs staff; the failure reason is the exception
      class name only (never a message that might hold personal data).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import smtplib
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from allauth.account.models import EmailAddress
from allauth.socialaccount.models import SocialAccount
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.models import Session
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.core.files.storage import default_storage
from django.core.mail import send_mail
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.authtoken.models import Token

from apps.core.app_service import AppService
from apps.users.models import (
    Announcement,
    CustomUser,
    MFADevice,
    Notification,
    NotificationPreferences,
    PushSubscription,
    SecurityQuestion,
)
from apps.users.models_erasure import AccountErasure
from apps.users.models_social import SocialPostingAccount, SocialProviderConfig
from apps.users.profile_models import ActivityFeed, Following, Profile, Reputation

logger = logging.getLogger(__name__)

#: Names every app may export from its ``api.py`` to take part in an erasure.
HANDLER_EXPORT: Final[str] = "anonymise_user"
BLOCKERS_EXPORT: Final[str] = "erasure_blockers"

#: Installed apps that hold per-person data and are therefore expected to export a
#: handler. Missing ones are reported as gaps, never silently skipped.
EXPECTED_HANDLER_APPS: Final[tuple[str, ...]] = (
    "ads",
    "blog",
    "bounty",
    "comments",
    "consent",
    "devices",
    "distribution",
    "firmwares",
    "gamification",
    "marketplace",
    "referral",
    "shop",
    "wallet",
)

PERMISSION: Final[str] = "users.anonymize_account"
CONFIRMATION_HOURS: Final[int] = 24
DEFAULT_GRACE_DAYS: Final[int] = 14
DEFAULT_MAX_ATTEMPTS: Final[int] = 3
DEFAULT_BLOCKED_MAX_DAYS: Final[int] = 30
BLOCKED_RETRY: Final[timedelta] = timedelta(days=1)
_REASON_MAX: Final[int] = 1000
_DB_SESSION_ENGINES: Final[tuple[str, ...]] = (
    "django.contrib.sessions.backends.db",
    "django.contrib.sessions.backends.cached_db",
)

#: What a handler may legitimately fail with; anything else is a bug and surfaces.
_HANDLER_ERRORS: Final[tuple[type[Exception], ...]] = (
    DatabaseError,
    ValidationError,
    ValueError,
    LookupError,
    TypeError,
    RuntimeError,
    OSError,
)


class ErasureRefused(Exception):
    """The request cannot be honoured; ``code`` says why (no retry will help)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ErasureBlocked(ErasureRefused):
    """Another app still needs the personal data; ``blockers`` maps app to reasons."""

    def __init__(self, blockers: dict[str, list[str]]) -> None:
        super().__init__("blocked")
        self.blockers = blockers

    @property
    def summary(self) -> str:
        """One line naming each blocking app and why (counts only, no personal data)."""
        return "; ".join(
            f"{label}: {', '.join(reasons)}" for label, reasons in self.blockers.items()
        )


REFUSAL_MESSAGES: Final[dict[str, str]] = {
    "already_anonymized": "This account has already been erased.",
    "privileged_account": (
        "Staff and administrator accounts must be demoted before they can be erased."
    ),
    "email_unverified": "Verify your email address before requesting erasure.",
    "inactive_account": "This account is not active.",
    "invalid_token": "This confirmation link is not valid.",
    "expired_token": "This confirmation link has expired. Request erasure again.",
    "not_open": "This erasure request is no longer open.",
    "wrong_account": "This request belongs to another account.",
    "reason_required": "Give the reason for the erasure.",
    "blocked": "Something must be resolved before this account can be erased.",
}


def grace_period() -> timedelta:
    """How long a confirmed self-service request waits before it runs."""
    days = int(getattr(settings, "ACCOUNT_ERASURE_GRACE_DAYS", DEFAULT_GRACE_DAYS))
    return timedelta(days=max(0, days))


def max_attempts() -> int:
    """How many times a failing erasure is tried before it needs staff."""
    return max(
        1, int(getattr(settings, "ACCOUNT_ERASURE_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))
    )


def blocked_max_days() -> int:
    """How long a blocked request waits for the blocker to clear before it fails."""
    return max(
        1,
        int(
            getattr(
                settings, "ACCOUNT_ERASURE_BLOCKED_MAX_DAYS", DEFAULT_BLOCKED_MAX_DAYS
            )
        ),
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def refusal_reason(user: CustomUser) -> str:
    """Why ``user`` cannot be erased right now, or an empty string."""
    if user.anonymized_at:
        return "already_anonymized"
    if user.is_staff or user.is_superuser:
        return "privileged_account"
    return ""


# ---------------------------------------------------------------------------
# The users part: scrub identity, delete credentials and personal rows
# ---------------------------------------------------------------------------
def installed_app_labels() -> list[str]:
    """Labels of the installed domain apps polled for an anonymiser (not ``users``)."""
    return sorted(
        config.label
        for config in django_apps.get_app_configs()
        if config.name.startswith("apps.") and config.label != "users"
    )


def _purge_sessions(user_pk: int) -> int:
    """Delete the account's stored sessions (database session engines only).

    Other engines cannot be searched by user; the account is inactive with an unusable
    password by then, which ends any such session at its next request.
    """
    if getattr(settings, "SESSION_ENGINE", "") not in _DB_SESSION_ENGINES:
        return 0
    target = str(user_pk)
    stale = [
        session.pk
        for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator(
            chunk_size=500
        )
        if session.get_decoded().get("_auth_user_id") == target
    ]
    deleted, _ = Session.objects.filter(pk__in=stale).delete()
    return deleted


def _delete_files_after_commit(names: list[str]) -> None:
    def remove() -> None:
        for name in names:
            try:
                default_storage.delete(name)
            except OSError:
                logger.warning("Erased avatar/cover file could not be removed")

    if names:
        transaction.on_commit(remove)


def _scrub_user(user: CustomUser, now: datetime) -> dict[str, int]:
    """Anonymise the users-owned data of ``user`` (the caller holds the row lock)."""
    pk = user.pk
    counts: dict[str, int] = {}

    profiles = list(Profile.objects.filter(user=user))
    _delete_files_after_commit(
        [
            image.name
            for profile in profiles
            for image in (profile.avatar, profile.cover)
            if image and image.name
        ]
    )

    def delete(label: str, queryset: Any) -> None:
        deleted, _ = queryset.delete()
        counts[label] = deleted

    delete("profiles", Profile.objects.filter(user=user))
    delete("reputation", Reputation.objects.filter(user=user))
    delete("follows", Following.objects.filter(Q(follower=user) | Q(following=user)))
    delete("activity", ActivityFeed.objects.filter(user=user))
    delete("notifications", Notification.objects.filter(recipient=user))
    delete(
        "notification_preferences", NotificationPreferences.objects.filter(user=user)
    )
    delete("push_subscriptions", PushSubscription.objects.filter(user=user))
    delete("security_questions", SecurityQuestion.objects.filter(user=user))
    delete("mfa_devices", MFADevice.objects.filter(user=user))
    delete("email_addresses", EmailAddress.objects.filter(user=user))
    delete("social_accounts", SocialAccount.objects.filter(user=user))
    delete("api_tokens", Token.objects.filter(user=user))
    counts["sessions"] = _purge_sessions(pk)

    counts["authored_notifications_detached"] = Notification.objects.filter(
        actor=user
    ).update(actor=None)
    counts["announcements_detached"] = Announcement.objects.filter(
        created_by=user
    ).update(created_by=None)
    SocialProviderConfig.objects.filter(created_by=user).update(created_by=None)
    SocialPostingAccount.objects.filter(created_by=user).update(created_by=None)

    user_type = ContentType.objects.get_for_model(CustomUser)
    counts["admin_log_entries"] = LogEntry.objects.filter(
        content_type=user_type, object_id=str(pk)
    ).update(object_repr=f"Erased account {pk}", change_message="")

    user.email = f"erased-{pk}@erased.invalid"
    user.username = f"erased-{pk}"
    user.full_name = ""
    user.bio = ""
    user.country = ""
    user.country_detected_at = None
    user.phone = None
    user.phone_country_code = ""
    user.currency = None
    user.role = None
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.can_create_blog_posts = False
    user.email_verified_at = None
    user.profile_completed = False
    user.username_changes_this_year = 0
    user.username_last_changed_at = None
    user.last_login = None
    user.set_unusable_password()
    user.anonymized_at = now
    user.save()
    user.groups.clear()
    user.user_permissions.clear()
    return counts


# ---------------------------------------------------------------------------
# Other apps: pull-based contract (``erasure_blockers`` then ``anonymise_user``)
# ---------------------------------------------------------------------------
def collect_blockers(user: CustomUser) -> dict[str, list[str]]:
    """Reasons other apps cannot be erased yet, per app label (empty when clear)."""
    blockers: dict[str, list[str]] = {}
    for label in installed_app_labels():
        check = AppService.export(label, BLOCKERS_EXPORT)
        if callable(check):
            reasons = [str(reason) for reason in check(user) or ()]
            if reasons:
                blockers[label] = reasons
    return blockers


def _clean_receipt(label: str, result: object) -> dict[str, int]:
    """Counts from an app's report (a mapping, a dataclass of numbers, or nothing)."""
    if result is None:
        return {}
    if is_dataclass(result) and not isinstance(result, type):
        result = asdict(result)
    if not isinstance(result, Mapping):
        raise TypeError(f"The {label} anonymiser must report counts.")
    return {
        str(key): int(value)
        for key, value in result.items()
        if isinstance(value, (int, bool))
    }


def _run_app_handlers(
    user: CustomUser,
) -> tuple[dict[str, dict[str, int]], list[str]]:
    receipts: dict[str, dict[str, int]] = {}
    gaps: list[str] = []
    for label in installed_app_labels():
        handler = AppService.export(label, HANDLER_EXPORT)
        if callable(handler):
            receipts[label] = _clean_receipt(label, handler(user))
        elif label in EXPECTED_HANDLER_APPS:
            gaps.append(label)
    return receipts, gaps


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _notify_erased(email: str) -> None:
    """Tell the former holder it is done; the address is never stored."""
    try:
        send_mail(
            "Your account has been erased",
            "Your account and personal data have been erased. You will not receive "
            "further email from us about it.",
            None,
            [email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, ImproperlyConfigured):
        logger.warning("Erasure confirmation email could not be sent")


def _claim(erasure_id: int) -> AccountErasure | None:
    """Count the attempt; ``None`` when there is nothing (left) to run."""
    with transaction.atomic():
        erasure = AccountErasure.objects.select_for_update().get(pk=erasure_id)
        if erasure.status != AccountErasure.Status.SCHEDULED:
            return None
        if erasure.attempts >= max_attempts():
            erasure.status = AccountErasure.Status.FAILED
            erasure.failure_reason = erasure.failure_reason or "attempts_exhausted"
            erasure.save(update_fields=["status", "failure_reason"])
            return None
        erasure.attempts += 1
        erasure.save(update_fields=["attempts"])
        return erasure


def _record_failure(erasure_id: int, reason: str, *, terminal: bool) -> None:
    status = (
        AccountErasure.Status.FAILED if terminal else AccountErasure.Status.SCHEDULED
    )
    AccountErasure.objects.filter(pk=erasure_id).update(
        status=status, failure_reason=reason[:160]
    )


def _record_blocked(erasure_id: int, blocked: ErasureBlocked) -> None:
    """Park a request that another app still blocks (not a failed attempt).

    It stays ``scheduled``, is re-checked daily and names the blocking apps; once
    ``ACCOUNT_ERASURE_BLOCKED_MAX_DAYS`` have passed since confirmation it becomes
    terminally ``failed`` for staff to resolve.
    """
    with transaction.atomic():
        erasure = AccountErasure.objects.select_for_update().get(pk=erasure_id)
        if erasure.status != AccountErasure.Status.SCHEDULED:
            return
        now = timezone.now()
        erasure.attempts = max(0, erasure.attempts - 1)
        erasure.receipt = {"blockers": blocked.blockers}
        erasure.failure_reason = f"blocked:{','.join(sorted(blocked.blockers))}"[:160]
        deadline = (erasure.confirmed_at or erasure.requested_at) + timedelta(
            days=blocked_max_days()
        )
        if now >= deadline:
            erasure.status = AccountErasure.Status.FAILED
            erasure.failure_reason = "blocked_timeout"
        else:
            erasure.scheduled_for = now + BLOCKED_RETRY
        erasure.save()


def execute_erasure(erasure_id: int) -> AccountErasure:
    """Run one scheduled request now; replaying it is harmless.

    Returns the request in its resulting state. A failed attempt leaves the account
    untouched (full rollback) and either stays ``scheduled`` for the next run or, once
    the attempts are used up or the refusal is final, becomes ``failed``. A request
    that another app still blocks waits (see ``_record_blocked``).
    """
    claimed = _claim(erasure_id)
    if claimed is None:
        return AccountErasure.objects.get(pk=erasure_id)

    try:
        with transaction.atomic():
            erasure = AccountErasure.objects.select_for_update().get(pk=erasure_id)
            if erasure.status != AccountErasure.Status.SCHEDULED:
                return erasure
            user = get_user_model().objects.select_for_update().get(pk=erasure.user_pk)
            now = timezone.now()
            original_email = user.email
            receipt: dict[str, Any] = {}
            if blockers := collect_blockers(user):
                raise ErasureBlocked(blockers)
            if user.anonymized_at is None:
                refusal = refusal_reason(user)
                if refusal:
                    raise ErasureRefused(refusal)
                receipt["users"] = _scrub_user(user, now)
            apps_receipt, gaps = _run_app_handlers(user)
            receipt["apps"] = apps_receipt
            receipt["gaps"] = gaps
            erasure.receipt = receipt
            erasure.status = (
                AccountErasure.Status.PARTIAL
                if gaps
                else AccountErasure.Status.COMPLETED
            )
            erasure.executed_at = now
            erasure.failure_reason = ""
            erasure.token_hash = ""
            erasure.user = user
            erasure.save()
            if erasure.source == AccountErasure.Source.SELF_SERVICE and receipt.get(
                "users"
            ):
                transaction.on_commit(lambda: _notify_erased(original_email))
    except ErasureBlocked as exc:
        logger.info(
            "Account erasure %s waits on: %s", erasure_id, ", ".join(exc.blockers)
        )
        _record_blocked(erasure_id, exc)
    except ErasureRefused as exc:
        logger.error("Account erasure %s refused: %s", erasure_id, exc.code)
        _record_failure(erasure_id, exc.code, terminal=True)
    except _HANDLER_ERRORS as exc:
        # Class name only: messages from the database or an app may hold personal data.
        logger.error(
            "Account erasure %s attempt %s failed: %s",
            erasure_id,
            claimed.attempts,
            type(exc).__name__,
        )
        _record_failure(
            erasure_id, type(exc).__name__, terminal=claimed.attempts >= max_attempts()
        )
    return AccountErasure.objects.get(pk=erasure_id)


def run_due_erasures(*, limit: int = 25) -> dict[str, int]:
    """Execute scheduled requests whose grace period is over (the periodic task)."""
    due = list(
        AccountErasure.objects.filter(
            status=AccountErasure.Status.SCHEDULED,
            scheduled_for__lte=timezone.now(),
        )
        .order_by("scheduled_for")
        .values_list("pk", flat=True)[:limit]
    )
    outcome = {"processed": 0, "completed": 0, "partial": 0, "failed": 0, "retry": 0}
    for erasure_id in due:
        result = execute_erasure(erasure_id)
        outcome["processed"] += 1
        if result.status == AccountErasure.Status.COMPLETED:
            outcome["completed"] += 1
        elif result.status == AccountErasure.Status.PARTIAL:
            outcome["partial"] += 1
        elif result.status == AccountErasure.Status.FAILED:
            outcome["failed"] += 1
        else:
            outcome["retry"] += 1
    return outcome


# ---------------------------------------------------------------------------
# Staff: immediate erasure
# ---------------------------------------------------------------------------
def anonymize_account(
    user: CustomUser, *, actor: CustomUser, reason: str
) -> AccountErasure:
    """Erase ``user`` now on behalf of staff (atomic, idempotent, audited).

    A request the person already made is taken over and run immediately. Calling it
    again for an erased account re-runs the app handlers (completing earlier gaps)
    and leaves the users part untouched.
    """
    if not actor.has_perm(PERMISSION):
        raise PermissionDenied("You may not erase accounts.")
    reason_text = (reason or "").strip()[:_REASON_MAX]
    if not reason_text:
        raise ErasureRefused("reason_required")
    now = timezone.now()
    with transaction.atomic():
        locked = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not locked.anonymized_at:
            refusal = refusal_reason(locked)
            if refusal:
                raise ErasureRefused(refusal)
        if blockers := collect_blockers(locked):
            raise ErasureBlocked(blockers)
        erasure = (
            AccountErasure.objects.select_for_update()
            .filter(user_pk=locked.pk, status__in=AccountErasure.OPEN_STATUSES)
            .first()
        )
        if erasure is None:
            erasure = AccountErasure(user=locked, user_pk=locked.pk)
        erasure.source = AccountErasure.Source.STAFF
        erasure.status = AccountErasure.Status.SCHEDULED
        erasure.requested_by = actor
        erasure.reason = reason_text
        erasure.confirmed_at = erasure.confirmed_at or now
        erasure.scheduled_for = now
        erasure.token_hash = ""
        erasure.save()
        erasure_id = erasure.pk
    return execute_erasure(erasure_id)


# ---------------------------------------------------------------------------
# Self-service: request -> email confirmation -> grace period -> task
# ---------------------------------------------------------------------------
def _send_confirmation_email(email: str, link: str) -> None:
    try:
        send_mail(
            "Confirm your account erasure request",
            "You asked us to erase your account and personal data.\n\n"
            f"Confirm within {CONFIRMATION_HOURS} hours: {link}\n\n"
            f"After you confirm, nothing happens for {grace_period().days} days so you "
            "can change your mind; you can cancel from your account until then.\n\n"
            "If you did not ask for this, ignore this email: nothing will be erased.",
            None,
            [email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException, ImproperlyConfigured):
        logger.error("Erasure confirmation email could not be sent")


def start_self_service_erasure(
    user: CustomUser, *, build_link: Callable[[str], str]
) -> AccountErasure:
    """Open (or refresh) the account holder's request and email the confirmation link.

    Idempotent: while a request is open the same one is returned (an unconfirmed one
    gets a fresh link; a confirmed one is left alone).
    """
    now = timezone.now()
    with transaction.atomic():
        locked = get_user_model().objects.select_for_update().get(pk=user.pk)
        refusal = refusal_reason(locked)
        if refusal:
            raise ErasureRefused(refusal)
        if not EmailAddress.objects.filter(
            user=locked, email__iexact=locked.email, verified=True
        ).exists():
            raise ErasureRefused("email_unverified")
        if blockers := collect_blockers(locked):
            raise ErasureBlocked(blockers)
        erasure = (
            AccountErasure.objects.select_for_update()
            .filter(user_pk=locked.pk, status__in=AccountErasure.OPEN_STATUSES)
            .first()
        )
        if erasure is not None and erasure.status == AccountErasure.Status.SCHEDULED:
            return erasure
        if erasure is None:
            erasure = AccountErasure(
                user=locked,
                user_pk=locked.pk,
                source=AccountErasure.Source.SELF_SERVICE,
                requested_by=locked,
            )
        token = secrets.token_urlsafe(32)
        erasure.token_hash = _hash_token(token)
        erasure.token_expires_at = now + timedelta(hours=CONFIRMATION_HOURS)
        erasure.save()
        email = locked.email
        link = build_link(token)
        transaction.on_commit(lambda: _send_confirmation_email(email, link))
    return erasure


def confirm_self_service_erasure(user: CustomUser, token: str) -> AccountErasure:
    """Confirm with the emailed token: the request is scheduled after the grace period."""
    now = timezone.now()
    with transaction.atomic():
        erasure = (
            AccountErasure.objects.select_for_update()
            .filter(
                user_pk=user.pk,
                source=AccountErasure.Source.SELF_SERVICE,
                status=AccountErasure.Status.AWAITING_CONFIRMATION,
            )
            .first()
        )
        if erasure is None or not erasure.token_hash:
            raise ErasureRefused("invalid_token")
        if not hmac.compare_digest(erasure.token_hash, _hash_token(token or "")):
            raise ErasureRefused("invalid_token")
        if erasure.token_expires_at is None or erasure.token_expires_at <= now:
            raise ErasureRefused("expired_token")
        erasure.status = AccountErasure.Status.SCHEDULED
        erasure.confirmed_at = now
        erasure.scheduled_for = now + grace_period()
        erasure.token_hash = ""
        erasure.token_expires_at = None
        erasure.save()
    return erasure


def cancel_erasure(erasure: AccountErasure, *, by: CustomUser) -> AccountErasure:
    """Withdraw an open request (the account holder, or staff with the permission)."""
    if by.pk != erasure.user_pk and not by.has_perm(PERMISSION):
        raise ErasureRefused("wrong_account")
    with transaction.atomic():
        locked = AccountErasure.objects.select_for_update().get(pk=erasure.pk)
        if not locked.is_open:
            raise ErasureRefused("not_open")
        locked.status = AccountErasure.Status.CANCELLED
        locked.token_hash = ""
        locked.token_expires_at = None
        locked.save()
    return locked


def open_erasure_for(user: CustomUser) -> AccountErasure | None:
    """The account's open request, if any (for the account page)."""
    return AccountErasure.objects.filter(
        user_pk=user.pk, status__in=AccountErasure.OPEN_STATUSES
    ).first()
