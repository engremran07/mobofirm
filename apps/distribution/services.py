"""Sharing pipeline: compose + enqueue (idempotent), schedule, approve, deliver with
retries, audit.

Failure semantics for one job:

* 2xx -> ``sent``.
* Network error, timeout, 408/425/429, 5xx -> retried with backoff
  (``RETRY_BACKOFF``; ``Retry-After`` is honoured) until ``channel.max_attempts``,
  then ``failed``.
* Any other 4xx, a blocked destination or an unusable configuration -> ``failed``
  immediately (retrying cannot help).
* Media a platform needs but the server cannot produce (no Pillow / no ffmpeg) -> the
  job waits as ``needs_media`` (attempt not consumed) and is dropped after
  ``MEDIA_WAIT_DAYS``; it is never reported as sent.
* ``CHANNEL_TRIP_AFTER`` consecutive failures switch the channel off with a visible
  reason (the circuit breaker) so a broken credential cannot hammer a platform.

Cross-app automation contract: owner = distribution; one transaction per job (job +
short link); idempotency key = ``<channel>:<kind>:<object>[:r<round>]``; audit = one
``ShareLog`` line per decision, attempt and staff action.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.core.app_service import app_enabled
from apps.distribution import (
    channels,
    composer,
    media,
    scheduling,
    shortlinks,
    variants,
)
from apps.distribution.channels import Outcome, SendContext
from apps.distribution.constants import (
    APPROVAL_REQUIRED,
    APPROVAL_SCHEDULE_ONLY,
    CHANNEL_TRIP_AFTER,
    CONTENT_BLOG,
    CONTENT_FIRMWARE,
    CONTENT_TEST,
    HOOK_EVERGREEN,
    LEASE_SECONDS,
    MEDIA_IMAGE,
    MEDIA_VIDEO,
    MEDIA_WAIT_DAYS,
    RETRY_BACKOFF,
)
from apps.distribution.content import Content
from apps.distribution.models import Campaign, Channel, ShareJob, ShareLog, ShortLink
from apps.seo.fetcher import SafeFetcher, default_fetcher
from apps.site_settings.crypto import (
    SecretUnavailableError,
    decrypt_secret,
    encrypt_secret,
)
from apps.site_settings.services import get_site_settings

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
MEDIA_RECHECK = timedelta(minutes=30)
PENDING_STATES = (
    ShareJob.Status.QUEUED,
    ShareJob.Status.PENDING,
    ShareJob.Status.NEEDS_MEDIA,
)

__all__ = [
    "Content",
    "Outcome",
    "Summary",
    "approve_job",
    "attempt",
    "cancel_job",
    "check_connection",
    "enqueue",
    "process_due_jobs",
    "reject_job",
    "reschedule_job",
    "retry_job",
    "send_test",
    "set_secret",
]


@dataclass(frozen=True, slots=True)
class Summary:
    sent: int = 0
    retried: int = 0
    failed: int = 0
    deferred: int = 0


def site_name() -> str:
    return get_site_settings().site_name


# -------------------------------------------------------------- credentials ----


def set_secret(channel: Channel, plaintext: str) -> None:
    """Store a credential encrypted (an empty value clears it)."""
    channel.secret_encrypted = encrypt_secret(plaintext)


def stored_values(channel: Channel) -> dict[str, str]:
    """Every stored value by field name; empty when the secret cannot be decrypted."""
    try:
        plain = decrypt_secret(channel.secret_encrypted)
    except SecretUnavailableError:
        plain = ""
    return channels.load_values(channel, plain)


def apply_values(
    channel: Channel, values: Mapping[str, str], *, clear_secrets: bool = False
) -> None:
    """Save form values: plain ones on the channel, secrets encrypted.

    A blank secret keeps the stored one, so a credential is never re-displayed.
    """
    adapter = channel.adapter
    if adapter is None:
        return
    current = {} if clear_secrets else stored_values(channel)
    options = dict(channel.options) if isinstance(channel.options, dict) else {}
    secrets: dict[str, str] = {}
    for spec in adapter.fields:
        value = (values.get(spec.name) or "").strip()
        if spec.store == "option":
            options[spec.name] = value
        elif spec.store == "target":
            channel.target = value
        elif spec.store == "endpoint_url":
            channel.endpoint_url = value
        else:
            secrets[spec.name] = value or current.get(spec.name, "")
    channel.options = options
    channel.secret_encrypted = encrypt_secret(channels.pack_secrets(adapter, secrets))


# --------------------------------------------------------------- templating ----


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def render_text(channel: Channel, content: Content) -> str:
    """Fill the channel template, trimming summary then title to the platform limit."""
    template = {
        CONTENT_FIRMWARE: channel.firmware_template,
        CONTENT_BLOG: channel.blog_template,
    }.get(content.kind, "{title}\n{url}")
    adapter = channel.adapter
    limit = adapter.message_limit if adapter else 2000
    values = {
        "title": content.title,
        "url": content.url,
        "summary": content.summary,
        "brand": content.brand,
        "model": content.model,
        "variant": content.variant,
        "kind": content.kind,
        "site": site_name(),
    }

    def fill() -> str:
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)

    text = fill()
    for field in ("summary", "title"):
        overflow = len(text) - limit
        if overflow <= 0:
            break
        values[field] = _shorten(values[field], len(values[field]) - overflow)
        text = fill()
    return _shorten(text, limit)


# ----------------------------------------------------------------- audit ----


def _log(
    channel: Channel,
    message: str,
    *,
    job: ShareJob | None = None,
    level: str = "info",
    code: str = "",
) -> None:
    ShareLog.objects.create(
        channel=channel,
        job=job,
        level=level,
        message=message[:400],
        response_code=code[:20],
    )


def _actor(user: Any) -> str:
    return f"staff #{user.pk}" if getattr(user, "pk", None) else "staff"


# ---------------------------------------------------------------- scheduling ----


def _wants(channel: Channel, kind: str) -> bool:
    return (kind == CONTENT_FIRMWARE and channel.share_firmware) or (
        kind == CONTENT_BLOG and channel.share_blog_posts
    )


def _recent_posts(channel: Channel, now: datetime) -> int:
    return ShareJob.objects.filter(
        channel=channel,
        status__in=[ShareJob.Status.SENT, *PENDING_STATES],
        created_at__gte=now - timedelta(hours=24),
    ).count()


def effective_daily_limit(channel: Channel) -> int:
    adapter = channel.adapter
    ceiling = adapter.daily_max if adapter else 0
    return min(channel.daily_limit, ceiling) if ceiling else channel.daily_limit


def _windows(channel: Channel) -> list[scheduling.Window]:
    windows = scheduling.parse_windows(channel.send_windows)
    adapter = channel.adapter
    if not windows and channel.approval_mode == APPROVAL_SCHEDULE_ONLY and adapter:
        windows = scheduling.parse_windows(adapter.default_windows)
    return windows


def fit_window(channel: Channel, moment: datetime) -> datetime:
    """The earliest instant >= ``moment`` inside the channel's windows and outside quiet
    hours."""
    return scheduling.next_allowed(
        moment,
        tz_name=channel.timezone,
        windows=_windows(channel),
        quiet_start=channel.quiet_start,
        quiet_end=channel.quiet_end,
    )


def _kind_count(channel: Channel, kind: str, start: datetime, end: datetime) -> int:
    return ShareJob.objects.filter(
        Q(
            status=ShareJob.Status.SENT,
            sent_at__gte=start,
            sent_at__lt=end,
        )
        | Q(
            status__in=PENDING_STATES,
            next_attempt_at__gte=start,
            next_attempt_at__lt=end,
        ),
        channel=channel,
        content_kind=kind,
    ).count()


def plan_slot(
    channel: Channel,
    kind: str,
    now: datetime,
    *,
    trending: bool = False,
) -> datetime:
    """When a new share may go out: after the delay, the platform's spacing and the
    queue (a trending launch skips the backlog), inside the windows, outside quiet hours,
    and under the per-day cap of its content type."""
    adapter = channel.adapter
    gap = timedelta(seconds=adapter.min_interval if adapter else 0)
    earliest = now + timedelta(minutes=channel.delay_minutes)
    if gap:
        if not trending:
            latest = ShareJob.objects.filter(
                channel=channel, status__in=PENDING_STATES
            ).aggregate(latest=Max("next_attempt_at"))["latest"]
            if latest:
                earliest = max(earliest, latest + gap)
        if channel.last_success_at:
            earliest = max(earliest, channel.last_success_at + gap)
    when = fit_window(channel, earliest)
    if channel.kind_daily_cap:
        for _day in range(7):
            start, end = scheduling.local_day_bounds(when, channel.timezone)
            if _kind_count(channel, kind, start, end) < channel.kind_daily_cap:
                break
            when = fit_window(channel, end)
    return when


# ------------------------------------------------------------------ enqueue ----


def _compose(
    channel: Channel,
    content: Content,
    now: datetime,
    *,
    reshare_round: int,
) -> tuple[str, str, str, str, str]:
    """(text, hook, short code, target url, link) for one share on one channel."""
    adapter = channel.adapter
    preset = channel.campaign or Campaign.default()
    if adapter is None or not channel.use_composer:
        return render_text(channel, content), "", "", "", content.url
    hook, _variant = (
        (HOOK_EVERGREEN, "")
        if reshare_round
        else variants.choose_hook(channel, preset, now=now)
    )
    referral = (
        shortlinks.referral_code(channel.referral_user)
        if preset.use_referral and channel.referral_user_id
        else ""
    )
    target = shortlinks.tracked_target(
        content.url,
        platform=channel.kind,
        campaign=preset.utm_campaign or content.kind,
        hook=hook,
        referral=referral,
    )
    code = ""
    link = target
    if preset.use_short_links:
        candidate = shortlinks.new_code()
        while ShortLink.objects.filter(code=candidate).exists():
            candidate = shortlinks.new_code()
        short = shortlinks.short_url(candidate)
        if short:
            code, link = candidate, short
    draft = composer.compose(
        content,
        adapter,
        hook=hook,
        link=link,
        site=site_name(),
        emoji=preset.emoji_policy,
        hashtag_cap=preset.hashtag_cap,
        evergreen=bool(reshare_round),
    )
    return draft.text, draft.hook, code, target, link


def _idempotency_key(channel: Channel, content: Content, reshare_round: int) -> str:
    suffix = f":r{reshare_round}" if reshare_round else ""
    return f"{channel.pk}:{content.kind}:{content.object_ref}{suffix}"[:200]


def enqueue(
    content: Content,
    *,
    now: datetime | None = None,
    reshare_round: int = 0,
    only: Channel | None = None,
) -> list[ShareJob]:
    """Queue ``content`` on every enabled, complete channel that wants it.

    Idempotent per (channel, object, round): replays create nothing. A channel over its
    daily limit records a ``skipped`` job so the decision stays visible. Channels in
    approval mode create ``pending`` jobs for staff to review. ``only`` restricts the
    delivery to one channel (evergreen re-shares).
    """
    moment = now or timezone.now()
    queued: list[ShareJob] = []
    if not app_enabled("distribution"):
        return queued
    rows = Channel.objects.filter(enabled=True).select_related(
        "campaign", "referral_user"
    )
    if only is not None:
        rows = rows.filter(pk=only.pk)
    for channel in rows:
        if not _wants(channel, content.kind) or channel.configuration_problem:
            continue
        key = _idempotency_key(channel, content, reshare_round)
        if ShareJob.objects.filter(idempotency_key=key).exists():
            continue
        job = _create_job(channel, content, key, moment, reshare_round)
        if job is not None and job.status != ShareJob.Status.SKIPPED:
            queued.append(job)
    return queued


def _create_job(
    channel: Channel,
    content: Content,
    key: str,
    moment: datetime,
    reshare_round: int,
) -> ShareJob | None:
    over = _recent_posts(channel, moment) >= effective_daily_limit(channel)
    if over:
        text, hook, code, target, link = (
            render_text(channel, content),
            "",
            "",
            "",
            content.url,
        )
        status = ShareJob.Status.SKIPPED
        when = moment
    else:
        text, hook, code, target, link = _compose(
            channel, content, moment, reshare_round=reshare_round
        )
        approval = channel.approval_mode == APPROVAL_REQUIRED
        status = ShareJob.Status.PENDING if approval else ShareJob.Status.QUEUED
        when = plan_slot(channel, content.kind, moment, trending=content.trending)
    try:
        with transaction.atomic():
            job = ShareJob.objects.create(
                channel=channel,
                content_kind=content.kind,
                object_ref=content.object_ref[:120],
                idempotency_key=key,
                title=content.title[:300],
                url=link[:500],
                target_url=target[:900],
                text=text,
                hook=hook,
                reshare_round=reshare_round,
                trending=content.trending,
                status=status,
                next_attempt_at=when,
                last_error="Daily limit reached" if over else "",
            )
            if code:
                ShortLink.objects.create(
                    code=code,
                    job=job,
                    channel=channel,
                    content_kind=content.kind,
                    object_ref=content.object_ref[:120],
                    hook=hook,
                    target_url=target,
                )
    except IntegrityError:
        return None
    note = {
        ShareJob.Status.SKIPPED: "Skipped: daily limit reached",
        ShareJob.Status.PENDING: "Awaiting staff approval",
    }.get(status, "Queued")
    _log(
        channel,
        note,
        job=job,
        level="warning" if over else "info",
    )
    return job


# ----------------------------------------------------------------- delivery ----


def classify(
    status: int, body: bytes, kind: str, headers: Mapping[str, str]
) -> Outcome:
    outcome = channels.classify_status(status, headers, body)
    if outcome.ok:
        return replace(outcome, external_id=channels.external_id(kind, body))
    return outcome


def _claim(job_id: int, now: datetime) -> ShareJob | None:
    """Lease a due job so concurrent workers never send the same one."""
    with transaction.atomic():
        job = (
            ShareJob.objects.select_for_update()
            .select_related("channel", "channel__campaign")
            .filter(
                pk=job_id,
                status__in=[ShareJob.Status.QUEUED, ShareJob.Status.NEEDS_MEDIA],
                next_attempt_at__lte=now,
                channel__enabled=True,
            )
            .first()
        )
        if job is None:
            return None
        job.attempts += 1
        job.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
        job.save(update_fields=["attempts", "next_attempt_at"])
        return job


def _trip_if_broken(channel: Channel) -> None:
    if channel.enabled and channel.consecutive_failures >= CHANNEL_TRIP_AFTER:
        channel.enabled = False
        channel.disabled_reason = (
            f"Switched off after {channel.consecutive_failures} failed posts in a row."
        )
        channel.save(update_fields=["enabled", "disabled_reason", "updated_at"])
        _log(channel, channel.disabled_reason, level="error")


def _finish(
    job: ShareJob, outcome: Outcome, now: datetime, *, final: bool = False
) -> str:
    channel = job.channel
    counted = job.content_kind != CONTENT_TEST
    job.response_code = outcome.code
    if outcome.ok:
        job.status = ShareJob.Status.SENT
        job.sent_at = now
        job.external_id = outcome.external_id
        job.last_error = ""
        if counted:
            channel.consecutive_failures = 0
            channel.last_error = ""
            channel.disabled_reason = ""
        channel.last_success_at = now
        _log(channel, "Sent", job=job, code=outcome.code)
        result = "sent"
    else:
        job.last_error = outcome.detail[:300]
        if counted:
            channel.last_error = outcome.detail[:300]
        if outcome.retryable and not final and job.attempts < channel.max_attempts:
            wait = max(
                outcome.retry_after,
                RETRY_BACKOFF[min(job.attempts, len(RETRY_BACKOFF)) - 1],
            )
            job.next_attempt_at = now + timedelta(seconds=wait)
            _log(
                channel,
                f"Attempt {job.attempts} failed ({outcome.detail}); retry in {wait}s",
                job=job,
                level="warning",
                code=outcome.code,
            )
            result = "retried"
        else:
            job.status = ShareJob.Status.FAILED
            if counted:
                channel.consecutive_failures += 1
            _log(
                channel,
                f"Gave up after {job.attempts} attempt(s): {outcome.detail}",
                job=job,
                level="error",
                code=outcome.code,
            )
            result = "failed"
    job.save(
        update_fields=[
            "status",
            "last_error",
            "response_code",
            "external_id",
            "sent_at",
            "next_attempt_at",
            "remote_state",
        ]
    )
    channel.save(
        update_fields=[
            "consecutive_failures",
            "last_success_at",
            "last_error",
            "disabled_reason",
            "updated_at",
        ]
    )
    if counted:
        _trip_if_broken(channel)
    return result


def _prepare_media(
    job: ShareJob, adapter: channels.Adapter, values: Mapping[str, str]
) -> tuple[channels.MediaRefs, str]:
    """Generate what the platform needs (best effort) and report what is still missing."""
    need = adapter.required_media(values)
    preset = job.channel.campaign or Campaign.default()
    wants_card = need == MEDIA_IMAGE or (
        adapter.image_optional and preset.generate_media and job.channel.use_composer
    )
    reasons: list[str] = []
    if wants_card or need == MEDIA_VIDEO:
        spec = media.spec_for(job.content_kind, job.object_ref, job.title, site_name())
        try:
            if wants_card:
                reasons.append(media.ensure_card(spec).reason)
            if need == MEDIA_VIDEO:
                reasons.append(media.ensure_video(spec).reason)
        except OSError as error:
            reasons.append(f"Storage error: {error}")
    refs = media.refs_for(job.content_kind, job.object_ref)
    problem = adapter.media_problem(refs, values)
    reason = next((r for r in reasons if r), "")
    return refs, (f"{problem} {reason}".strip() if problem else "")


def _defer_for_media(job: ShareJob, problem: str, now: datetime) -> str:
    """Park a job that lacks media without spending one of its attempts."""
    channel = job.channel
    first = job.status != ShareJob.Status.NEEDS_MEDIA
    job.attempts = max(job.attempts - 1, 0)
    job.last_error = problem[:300]
    if job.created_at <= now - timedelta(days=MEDIA_WAIT_DAYS):
        job.status = ShareJob.Status.SKIPPED
        _log(channel, f"Dropped: waited too long for media ({problem})", job=job)
    else:
        job.status = ShareJob.Status.NEEDS_MEDIA
        job.next_attempt_at = now + MEDIA_RECHECK
        if first:
            _log(channel, f"Waiting for media: {problem}", job=job, level="warning")
    job.save(update_fields=["status", "attempts", "last_error", "next_attempt_at"])
    return "waiting"


def _persist_updated(
    channel: Channel,
    adapter: channels.Adapter,
    values: dict[str, str],
    updated: Mapping[str, str],
) -> None:
    """Keep a rotated refresh token so the next send still works."""
    merged = {**values, **updated}
    channel.secret_encrypted = encrypt_secret(channels.pack_secrets(adapter, merged))
    channel.save(update_fields=["secret_encrypted", "updated_at"])


def _context(
    channel: Channel, job: ShareJob, fetcher: SafeFetcher | None
) -> tuple[SendContext | None, Outcome | None]:
    adapter = channel.adapter
    if adapter is None:
        return None, Outcome(
            False, False, "config", f"Unknown platform {channel.kind}."
        )
    try:
        plain = decrypt_secret(channel.secret_encrypted)
    except SecretUnavailableError as error:
        return None, Outcome(False, False, "secret", str(error))
    values = channels.load_values(channel, plain)
    problem = adapter.problem(values)
    if problem:
        return None, Outcome(False, False, "config", problem)
    secrets = [values.get(spec.name, "") for spec in adapter.secret_fields()]
    http = channels.Http(fetcher or default_fetcher(), [*secrets, plain])
    return SendContext(channel, job, values, http), None


def _run(
    job: ShareJob, now: datetime, fetcher: SafeFetcher | None, *, final: bool = False
) -> str:
    """Deliver a job whose attempt was already counted (leased)."""
    channel = job.channel
    ctx, failure = _context(channel, job, fetcher)
    adapter = channel.adapter
    if ctx is None or adapter is None:
        return _finish(
            job, failure or Outcome(False, False, "config", "Not ready"), now
        )
    ctx.media, problem = _prepare_media(job, adapter, ctx.values)
    if problem:
        if job.content_kind == CONTENT_TEST:
            return _finish(
                job, Outcome(False, False, "media", problem), now, final=True
            )
        return _defer_for_media(job, problem, now)
    try:
        outcome = adapter.publish(ctx)
    except (channels.ChannelConfigError, KeyError, TypeError, ValueError) as error:
        outcome = Outcome(False, False, "config", ctx.http.scrub(str(error)))
    if ctx.updated:
        _persist_updated(channel, adapter, ctx.values, ctx.updated)
    return _finish(job, outcome, now, final=final)


def attempt(
    job_id: int, *, now: datetime | None = None, fetcher: SafeFetcher | None = None
) -> str:
    """One delivery attempt: ``sent``, ``retried``, ``failed``, ``waiting`` (needs
    media) or ``""`` (not due)."""
    moment = now or timezone.now()
    job = _claim(job_id, moment)
    if job is None:
        return ""
    return _run(job, moment, fetcher)


def _too_soon(job: ShareJob, now: datetime) -> datetime | None:
    """When the platform's minimum gap since the last post ends (None = go ahead)."""
    adapter = job.channel.adapter
    last = job.channel.last_success_at
    if adapter is None or not adapter.min_interval or last is None:
        return None
    ready = last + timedelta(seconds=adapter.min_interval)
    return ready if ready > now else None


def process_due_jobs(
    *, limit: int = 25, now: datetime | None = None, fetcher: SafeFetcher | None = None
) -> Summary:
    """Deliver every due job (the scheduler calls this every minute)."""
    moment = now or timezone.now()
    if not app_enabled("distribution"):
        return Summary()
    due = list(
        ShareJob.objects.filter(
            status__in=[ShareJob.Status.QUEUED, ShareJob.Status.NEEDS_MEDIA],
            next_attempt_at__lte=moment,
            channel__enabled=True,
        )
        .select_related("channel")
        .order_by("next_attempt_at", "id")[:limit]
    )
    counts = {"sent": 0, "retried": 0, "failed": 0, "deferred": 0}
    for job in due:
        channel = Channel.objects.get(pk=job.channel_id)
        job.channel = channel
        ready = _too_soon(job, moment)
        if ready is not None:
            ShareJob.objects.filter(pk=job.pk).update(next_attempt_at=ready)
            counts["deferred"] += 1
            continue
        result = attempt(job.pk, now=moment, fetcher=fetcher)
        if result in counts:
            counts[result] += 1
    return Summary(**counts)


# --------------------------------------------------------------- staff tools ----


def send_test(channel: Channel, *, fetcher: SafeFetcher | None = None) -> ShareJob:
    """Post a labelled test message right now (works while the channel is off)."""
    moment = timezone.now()
    site = get_site_settings()
    adapter = channel.adapter
    limit = adapter.message_limit if adapter else 500
    stamp = moment.strftime("%Y%m%d%H%M%S%f")
    text = f"Test post from {site.site_name} ({stamp[:8]})."
    job = ShareJob.objects.create(
        channel=channel,
        content_kind=CONTENT_TEST,
        object_ref=f"test-{stamp}",
        idempotency_key=f"{channel.pk}:test:{stamp}",
        title="Test post",
        url=site.site_url,
        text=_shorten(text, limit),
        status=ShareJob.Status.QUEUED,
        attempts=1,
        next_attempt_at=moment,
    )
    _log(channel, "Test post", job=job)
    problem = channel.configuration_problem
    if problem:
        _finish(job, Outcome(False, False, "config", problem), moment, final=True)
    else:
        _run(job, moment, fetcher, final=True)
    job.refresh_from_db()
    return job


def check_connection(
    channel: Channel, *, fetcher: SafeFetcher | None = None
) -> Outcome:
    """Validate the credentials with a read-only platform call and record the result."""
    adapter = channel.adapter
    moment = timezone.now()
    probe = ShareJob(channel=channel, content_kind=CONTENT_TEST)
    ctx, failure = _context(channel, probe, fetcher)
    if ctx is None or adapter is None:
        outcome = failure or Outcome(False, False, "config", "Not ready")
    elif not adapter.supports_check:
        outcome = Outcome(
            False,
            False,
            "unsupported",
            f"{adapter.label} has no read-only check; use “Send test post”.",
        )
    else:
        try:
            outcome = adapter.check(ctx)
        except (channels.ChannelConfigError, KeyError, TypeError, ValueError) as error:
            outcome = Outcome(False, False, "config", ctx.http.scrub(str(error)))
        if ctx.updated:
            _persist_updated(channel, adapter, ctx.values, ctx.updated)
    channel.connection_checked_at = moment
    channel.connection_ok = None if outcome.code == "unsupported" else outcome.ok
    channel.connection_message = (outcome.detail or outcome.code)[:300]
    channel.save(
        update_fields=[
            "connection_checked_at",
            "connection_ok",
            "connection_message",
            "updated_at",
        ]
    )
    _log(
        channel,
        f"Connection test: {channel.connection_message}",
        level="info" if outcome.ok else "warning",
        code=outcome.code,
    )
    return outcome


def retry_job(job: ShareJob) -> bool:
    """Staff: queue a failed or skipped job again with fresh attempts."""
    if job.status not in {ShareJob.Status.FAILED, ShareJob.Status.SKIPPED}:
        return False
    job.status = ShareJob.Status.QUEUED
    job.attempts = 0
    job.next_attempt_at = timezone.now()
    job.last_error = ""
    job.save(update_fields=["status", "attempts", "next_attempt_at", "last_error"])
    _log(job.channel, "Retry requested by staff", job=job)
    return True


def cancel_job(job: ShareJob, user: Any = None) -> bool:
    if job.status not in PENDING_STATES:
        return False
    job.status = ShareJob.Status.CANCELLED
    job.save(update_fields=["status"])
    _log(job.channel, f"Cancelled by {_actor(user)}", job=job)
    return True


def approve_job(
    job: ShareJob,
    user: Any,
    *,
    text: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Staff approves a pending share (optionally editing the text first); it is then
    scheduled into the channel's next allowed slot."""
    if job.status != ShareJob.Status.PENDING:
        return False
    moment = now or timezone.now()
    adapter = job.channel.adapter
    if text is not None and text.strip():
        edited = text.strip()
        limit = adapter.message_limit if adapter else len(edited)
        if (
            channels.visible_length(edited, adapter.url_length if adapter else 0)
            > limit
        ):
            raise ValueError(f"The text is longer than the {limit} characters allowed.")
        if edited != job.text:
            job.text = edited
            _log(job.channel, f"Text edited by {_actor(user)}", job=job)
    job.status = ShareJob.Status.QUEUED
    job.decided_by = user if getattr(user, "pk", None) else None
    job.decided_at = moment
    job.next_attempt_at = fit_window(job.channel, max(moment, job.next_attempt_at))
    job.save(
        update_fields=[
            "text",
            "status",
            "decided_by",
            "decided_at",
            "next_attempt_at",
        ]
    )
    _log(job.channel, f"Approved by {_actor(user)}", job=job)
    return True


def reject_job(job: ShareJob, user: Any, note: str = "") -> bool:
    if job.status != ShareJob.Status.PENDING:
        return False
    job.status = ShareJob.Status.REJECTED
    job.decided_by = user if getattr(user, "pk", None) else None
    job.decided_at = timezone.now()
    job.decision_note = note[:300]
    job.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    _log(job.channel, f"Rejected by {_actor(user)}: {note}"[:400], job=job)
    return True


def reschedule_job(job: ShareJob, when: datetime, user: Any = None) -> bool:
    """Staff moves a waiting share to an explicit time (their choice wins over windows)."""
    if job.status not in PENDING_STATES:
        return False
    job.next_attempt_at = when
    job.save(update_fields=["next_attempt_at"])
    _log(
        job.channel, f"Rescheduled to {when:%Y-%m-%d %H:%M} by {_actor(user)}", job=job
    )
    return True


# ------------------------------------------------------------------ preview ----


@dataclass(frozen=True, slots=True)
class PreviewResult:
    preview: channels.Preview
    hook: str
    link: str
    title: str
    sample: bool


def sample_content() -> Content:
    origin = get_site_settings().site_url.rstrip("/")
    return Content(
        kind=CONTENT_FIRMWARE,
        object_ref="preview:sample",
        title="Acme Phone One Global",
        url=f"{origin}/firmwares/",
        summary="B123",
        brand="Acme",
        model="Phone One",
        variant="Global",
        chipset="Mediatek Helio G100",
        android_version="14",
        build_number="B123",
        size_bytes=3 * 1024**3,
        has_guide=True,
    )


def build_preview(
    channel: Channel, item: Content | None = None, *, now: datetime | None = None
) -> PreviewResult | None:
    """Dry run: the post this channel would write for ``item`` (or a sample)."""
    adapter = channel.adapter
    if adapter is None:
        return None
    moment = now or timezone.now()
    sample = item is None
    item = item or sample_content()
    preset = channel.campaign or Campaign.default()
    hook, _variant = variants.choose_hook(channel, preset, now=moment)
    link = shortlinks.short_url("preview") or item.url
    if channel.use_composer:
        draft = composer.compose(
            item,
            adapter,
            hook=hook,
            link=link,
            site=site_name(),
            emoji=preset.emoji_policy,
            hashtag_cap=preset.hashtag_cap,
        )
        text, used_hook = draft.text, draft.hook
    else:
        text, used_hook = render_text(channel, item), ""
    values = stored_values(channel)
    refs = media.refs_for(item.kind, item.object_ref)
    caps = media.capabilities()
    if not refs.image_url and caps["cards"]:
        refs.image_url = "(share card is generated when sent)"
    if not refs.video_url and caps["video"]:
        refs.video_url = "(video is generated when sent)"
    return PreviewResult(
        adapter.preview(text, values, refs),
        used_hook,
        link,
        item.title,
        sample,
    )
