"""Motion video pipeline: enlist, render, review, approve, post.

States: ``draft`` -> ``rendering`` -> ``ready`` -> ``approved`` -> ``posted``, with
``rejected`` and ``failed`` as side exits. Rules that are enforced here and tested:

* **Nothing posts without approval.** Video platforms read a video only through
  :func:`approved_refs`, which returns the file of an ``approved`` job and nothing else.
* **Idempotent.** One job per (content, template, version, preset, hook); a replayed event,
  a double click or a bulk import creates no second draft.
* **Rate limited.** Automatic drafts stop at ``video_daily_cap`` per day.
* **Bounded.** A render runs under ``video_render_timeout`` (everything is killed on
  timeout), is retried at most ``MAX_ATTEMPTS`` times and then stays ``failed``.
* **After commit.** Publish handlers enlist inside ``transaction.on_commit``.
"""

from __future__ import annotations

import logging
import secrets
import subprocess
import sys
import tempfile
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.distribution import content as content_module
from apps.distribution.channels import MediaRefs
from apps.distribution.models import (
    Channel,
    DistributionSettings,
    ShareJob,
    ShareLog,
    VideoJob,
)
from apps.distribution.video import audio, capabilities, packs, plan, render, sources
from apps.seo.siteurls import absolute_url
from apps.site_settings.services import get_site_settings

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
STALE_GRACE = 180
VOICE_PAD_MS = 350
LOG_LIMIT = 4000
Status = VideoJob.Status
MUTABLE = (Status.DRAFT, Status.READY, Status.REJECTED, Status.FAILED)
REVIEWABLE = (Status.READY,)
KEEP_FILES = (Status.APPROVED,)


def _log(message: str, *, level: str = "info", channel: Channel | None = None) -> None:
    ShareLog.objects.create(channel=channel, level=level, message=message[:400])


def _actor(user: Any) -> str:
    return getattr(user, "username", "") or "system"


def brand() -> plan.Brand:
    site = get_site_settings()
    host = absolute_url("/").split("//", 1)[-1].strip("/")
    return plan.Brand(site.site_name or "Mobofirm", host or "mobofirm")


def snapshot(source: sources.Source) -> dict[str, Any]:
    return {"source": asdict(source)}


def source_from(params: dict[str, Any]) -> sources.Source:
    """Rebuild the stored source (lists back to tuples)."""
    raw = dict(params.get("source") or {})
    kinds = {f.name for f in fields(sources.Source)}
    data = {k: v for k, v in raw.items() if k in kinds}
    for name in ("tags", "points", "steps"):
        data[name] = tuple(data.get(name) or ())
    data["items"] = tuple(tuple(i) for i in data.get("items") or ())
    data["bars"] = tuple(tuple(b) for b in data.get("bars") or ())
    return sources.Source(**data)


def key_for(kind: str, ref: str, pack: packs.Pack, preset: str, hook: str) -> str:
    return f"{kind}:{ref}:{pack.slug}:v{pack.version}:{preset}:{hook}"[:240]


# ------------------------------------------------------------------ creation ----


def _today_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def auto_budget(now: datetime | None = None) -> int:
    """How many more automatic drafts today's cap allows."""
    moment = now or timezone.now()
    cap = DistributionSettings.get_solo().video_daily_cap
    used = VideoJob.objects.filter(
        created_at__gte=_today_start(moment), params__auto=True
    ).count()
    return max(0, cap - used)


def create(
    kind: str,
    ref: str,
    *,
    pack_slug: str = "",
    hook: str = "A",
    preset: str = "9x16",
    auto: bool = False,
    source: sources.Source | None = None,
    now: datetime | None = None,
) -> tuple[VideoJob | None, str]:
    """A draft for ``kind``/``ref``; returns ``(job, reason)`` (``job`` is ``None`` when
    nothing was created: unknown content, cap reached, unknown template)."""
    if hook not in {"A", "B"} or preset not in plan.PRESETS:
        return None, "Unknown hook or preset."
    found = source or sources.load(kind, ref)
    if found is None:
        return None, "That content is not public (or no longer exists)."
    pack = packs.PACKS.get(pack_slug) if pack_slug else packs.default_pack(found)
    if pack is None or pack.slug == "smoke":
        return None, "Unknown template."
    if kind not in pack.kinds:
        return None, "That template does not fit this content."
    key = key_for(kind, ref, pack, preset, hook)
    existing = VideoJob.objects.filter(idempotency_key=key).first()
    if existing is not None:
        return existing, "A video for this content and template already exists."
    if auto and auto_budget(now) <= 0:
        return None, "The daily limit for automatic drafts is reached."
    params = snapshot(found)
    params["auto"] = auto
    try:
        with transaction.atomic():
            job = VideoJob.objects.create(
                content_kind=kind,
                object_ref=ref[:120],
                title=found.title[:300],
                template=pack.slug,
                template_version=pack.version,
                preset=preset,
                hook=hook,
                params=params,
                idempotency_key=key,
            )
    except IntegrityError:
        return VideoJob.objects.filter(idempotency_key=key).first(), "Already exists."
    _log(
        f"Video draft created: {pack.slug} for {kind} {ref}"
        + (" (automatic)" if auto else "")
    )
    return job, ""


def enlist(
    item: content_module.Content, *, now: datetime | None = None
) -> VideoJob | None:
    """Event path: one automatic draft for newly public firmware or a post."""
    cfg = DistributionSettings.get_solo()
    if not cfg.video_auto_enlist or cfg.video_daily_cap <= 0:
        return None
    job, _reason = create(
        item.kind,
        item.object_ref,
        auto=True,
        source=sources.from_content(item),
        now=now,
    )
    if (
        job is not None
        and job.status == Status.DRAFT
        and cfg.video_auto_render
        and capabilities.can_render()
    ):
        request_render(job)
    return job


def enlist_weekly_roundup(now: datetime | None = None) -> VideoJob | None:
    """At most one roundup draft per ISO week, once at least three firmware landed."""
    moment = now or timezone.now()
    iso = moment.isocalendar()
    ref = f"week:{iso.year}-W{iso.week:02d}"
    source = sources.load(sources.KIND_ROUNDUP, ref)
    if source is None or len(source.items) < 3:
        return None
    job, _reason = create(
        sources.KIND_ROUNDUP, ref, auto=True, source=source, now=moment
    )
    return job


# ------------------------------------------------------------------ rendering ----


def _dispatch(job_id: int) -> None:
    """Run the render outside the web request: Celery when configured, else a detached
    local process (development without a worker)."""
    from django.conf import settings

    if str(getattr(settings, "QUEUE_BACKEND", "sync")) == "celery":
        from apps.distribution import tasks

        tasks.render_video.delay(job_id)
        return
    manage = Path(settings.BASE_DIR) / "manage.py"
    subprocess.Popen(
        [sys.executable, str(manage), "distribution_jobs", "--render", str(job_id)],
        cwd=str(settings.BASE_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def request_render(job: VideoJob, *, dispatch: Any = None) -> bool:
    """Queue a render (idempotent): only a job in an editable state can start one."""
    with transaction.atomic():
        locked = VideoJob.objects.select_for_update().get(pk=job.pk)
        if locked.status not in MUTABLE:
            return False
        if not capabilities.can_render() and dispatch is None:
            locked.status = Status.FAILED
            locked.error = "Rendering tools are missing; see the capability report."
            locked.save(update_fields=["status", "error", "updated_at"])
            return False
        locked.status = Status.RENDERING
        locked.rendering_started_at = None
        locked.error = ""
        locked.save(
            update_fields=["status", "rendering_started_at", "error", "updated_at"]
        )
    job.refresh_from_db()
    send = dispatch or _dispatch
    transaction.on_commit(lambda: send(job.pk))
    return True


def _claim(job_id: int, now: datetime) -> VideoJob | None:
    with transaction.atomic():
        job = (
            VideoJob.objects.select_for_update()
            .filter(pk=job_id, status=Status.RENDERING)
            .first()
        )
        if job is None or job.rendering_started_at is not None:
            return None
        job.rendering_started_at = now
        job.attempts += 1
        job.save(update_fields=["rendering_started_at", "attempts", "updated_at"])
        return job


def _pack(job: VideoJob) -> packs.Pack:
    pack = packs.PACKS.get(job.template)
    if pack is None or pack.version != job.template_version:
        raise render.RenderError(
            f"Template {job.template} v{job.template_version} is no longer available; create a new video."
        )
    return pack


def build_plan(
    job: VideoJob, voices: list[audio.Voice] | None = None
) -> dict[str, Any]:
    pack = _pack(job)
    timing = {v.beat_id: v.ms + VOICE_PAD_MS for v in voices or []}
    draft = pack.build(source_from(job.params), job.hook, brand())
    return plan.build_plan(
        draft,
        preset=job.preset,
        brand=brand(),
        beat_ms=timing,
        min_ms=pack.min_ms,
        max_ms=pack.max_ms,
    )


def _store(job: VideoJob, result: render.RenderResult) -> None:
    token = job.media_token or secrets.token_hex(16)
    video_name = f"distribution/videos/{token}/video.mp4"
    poster_name = f"distribution/videos/{token}/poster.jpg"
    for name in (video_name, poster_name):
        if default_storage.exists(name):
            default_storage.delete(name)
    with result.path.open("rb") as handle:
        default_storage.save(video_name, ContentFile(handle.read()))
    if result.poster:
        default_storage.save(poster_name, ContentFile(result.poster))
    job.media_token = token
    job.video_name = video_name
    job.poster_name = poster_name if result.poster else ""


def delete_files(job: VideoJob) -> None:
    for name in (job.video_name, job.poster_name):
        if name:
            try:
                default_storage.delete(name)
            except OSError:
                logger.warning("Could not delete %s", name)
    job.video_name = job.poster_name = ""


def render_job(
    job_id: int,
    *,
    now: datetime | None = None,
    frames_factory: Any = None,
    encoder: Any = None,
    workers: int | None = None,
    voice: bool = True,
    with_audio: bool = True,
) -> str:
    """Render a claimed job: ``ready``, ``failed``, ``retry`` or ``''`` (nothing to do)."""
    moment = now or timezone.now()
    job = _claim(job_id, moment)
    if job is None:
        return ""
    cfg = DistributionSettings.get_solo()
    try:
        voices: list[audio.Voice] = []
        with tempfile.TemporaryDirectory(prefix="video-voice-") as folder:
            if voice and capabilities.tts_engine():
                draft_plan = build_plan(job)
                voices = audio.synthesize_voice(
                    [(b["id"], b["text"]) for b in draft_plan["beats"]], Path(folder)
                )
            final_plan = build_plan(job, voices)
            result = render.render_video(
                final_plan,
                Path(folder) / "out",
                timeout_s=cfg.video_render_timeout,
                voices=voices,
                frames_factory=frames_factory,
                encoder=encoder,
                workers=workers,
                with_audio=with_audio,
            )
            delete_files(job)
            _store(job, result)
    except (render.RenderError, plan.PlanError, OSError) as error:
        retryable = (
            bool(getattr(error, "retryable", False)) and job.attempts < MAX_ATTEMPTS
        )
        job.error = str(error)[:300]
        job.render_log = f"attempt {job.attempts}: {error}"[:LOG_LIMIT]
        job.status = Status.RENDERING if retryable else Status.FAILED
        job.rendering_started_at = None if retryable else job.rendering_started_at
        job.save(
            update_fields=[
                "error",
                "render_log",
                "status",
                "rendering_started_at",
                "updated_at",
            ]
        )
        _log(
            f"Video render failed ({'will retry' if retryable else 'gave up'}): {error}",
            level="warning" if retryable else "error",
        )
        if retryable:
            transaction.on_commit(lambda: _dispatch(job.pk))
            return "retry"
        return "failed"
    job.status = Status.READY
    job.duration_ms = result.duration_ms
    job.size_bytes = result.size_bytes
    job.checksum = result.checksum
    job.render_log = result.log
    job.error = ""
    job.rendered_at = timezone.now()
    job.save()
    _log(
        f"Video rendered: {job.title} ({result.duration_ms // 1000} s, {result.size_bytes // 1024} KiB)"
    )
    return "ready"


def sweep_stale(now: datetime | None = None) -> int:
    """Fail renders whose process vanished (no heartbeat within the timeout + grace)."""
    moment = now or timezone.now()
    cfg = DistributionSettings.get_solo()
    limit = timedelta(seconds=cfg.video_render_timeout + STALE_GRACE)
    stale = VideoJob.objects.filter(
        status=Status.RENDERING, rendering_started_at__lt=moment - limit
    )
    count = 0
    for job in stale:
        job.status = Status.FAILED
        job.error = "The render stopped without finishing."
        job.save(update_fields=["status", "error", "updated_at"])
        count += 1
    queued = VideoJob.objects.filter(
        status=Status.RENDERING,
        rendering_started_at__isnull=True,
        updated_at__lt=moment - limit,
    )
    for job in queued:
        job.status = Status.FAILED
        job.error = "The render never started (is a worker running?)."
        job.save(update_fields=["status", "error", "updated_at"])
        count += 1
    return count


# ------------------------------------------------------------------- review ----


def approve(job: VideoJob, user: Any, *, now: datetime | None = None) -> bool:
    """Staff approves a rendered video; waiting shares for the content are released."""
    moment = now or timezone.now()
    updated = (
        VideoJob.objects.filter(pk=job.pk, status=Status.READY)
        .exclude(video_name="")
        .update(
            status=Status.APPROVED,
            decided_by=user if getattr(user, "pk", None) else None,
            decided_at=moment,
            decision_note="",
        )
    )
    if not updated:
        return False
    job.refresh_from_db()
    released = ShareJob.objects.filter(
        content_kind=job.content_kind,
        object_ref=job.object_ref,
        status=ShareJob.Status.NEEDS_MEDIA,
    ).update(next_attempt_at=moment)
    _log(
        f"Video approved by {_actor(user)}: {job.title}; {released} waiting post(s) released"
    )
    return True


def reject(job: VideoJob, user: Any, note: str = "") -> bool:
    updated = VideoJob.objects.filter(
        pk=job.pk, status__in=(Status.READY, Status.APPROVED)
    ).update(
        status=Status.REJECTED,
        decided_by=user if getattr(user, "pk", None) else None,
        decided_at=timezone.now(),
        decision_note=note[:300],
    )
    if updated:
        _log(
            f"Video rejected by {_actor(user)}: {job.title}: {note}"[:400],
            level="warning",
        )
    return bool(updated)


def rerender(
    job: VideoJob,
    user: Any,
    *,
    pack_slug: str = "",
    hook: str = "",
    dispatch: Any = None,
) -> tuple[VideoJob | None, str]:
    """Render again, optionally with another template or hook (a new idempotent job)."""
    slug = pack_slug or job.template
    chosen_hook = hook or job.hook
    if slug == job.template and chosen_hook == job.hook:
        if job.status not in MUTABLE:
            return None, "This video cannot be rendered again in its current state."
        delete_files(job)
        job.checksum = ""
        job.attempts = 0
        job.save(
            update_fields=[
                "video_name",
                "poster_name",
                "checksum",
                "attempts",
                "updated_at",
            ]
        )
        target = job
    else:
        target, reason = create(
            job.content_kind,
            job.object_ref,
            pack_slug=slug,
            hook=chosen_hook,
            preset=job.preset,
            source=source_from(job.params),
        )
        if target is None:
            return None, reason
    started = request_render(target, dispatch=dispatch)
    _log(f"Video re-render requested by {_actor(user)}: {target.title}")
    return (target, "") if started else (target, "It could not be queued.")


def schedule(job: VideoJob, channel_ids: list[int]) -> int:
    """Queue the approved video's content on the chosen channels (idempotent per channel)."""
    if job.status not in (Status.APPROVED, Status.POSTED):
        return 0
    item = content_module.load(job.content_kind, job.object_ref)
    if item is None:
        return 0
    from apps.distribution import services

    count = 0
    for channel in Channel.objects.filter(pk__in=channel_ids, enabled=True):
        count += len(services.enqueue(item, only=channel))
    if count:
        _log(f"Video scheduled to {count} channel post(s): {job.title}")
    return count


def approved_job(kind: str, ref: str) -> VideoJob | None:
    return (
        VideoJob.objects.filter(
            content_kind=kind,
            object_ref=ref,
            status__in=(Status.APPROVED, Status.POSTED),
        )
        .exclude(video_name="")
        .order_by("-decided_at", "-id")
        .first()
    )


def _read(name: str) -> bytes:
    with default_storage.open(name, "rb") as handle:
        return handle.read()


def approved_refs(kind: str, ref: str) -> MediaRefs | None:
    """Media for a video platform: only an approved video, else ``None`` (never anything else)."""
    job = approved_job(kind, ref)
    if job is None or not default_storage.exists(job.video_name):
        return None
    return MediaRefs(
        video_url=absolute_url(default_storage.url(job.video_name)),
        video_name=job.video_name,
        reader=_read,
    )


def note_posted(kind: str, ref: str) -> None:
    """A video platform delivered the approved video: record it."""
    updated = VideoJob.objects.filter(
        content_kind=kind, object_ref=ref, status=Status.APPROVED
    ).update(status=Status.POSTED)
    if updated:
        _log(f"Video posted: {kind} {ref}")


# ---------------------------------------------------------------- retention ----


def purge_expired(days: int, now: datetime | None = None) -> int:
    """Delete rendered files older than ``days`` except approved ones (0 keeps all)."""
    if days <= 0:
        return 0
    cutoff = (now or timezone.now()) - timedelta(days=days)
    removed = 0
    for job in VideoJob.objects.filter(created_at__lt=cutoff).exclude(
        status__in=(*KEEP_FILES, Status.RENDERING)
    ):
        delete_files(job)
        if job.status in (Status.READY,):
            job.status = Status.DRAFT
            job.save(
                update_fields=["video_name", "poster_name", "status", "updated_at"]
            )
        elif job.status in (Status.REJECTED, Status.FAILED, Status.DRAFT):
            job.delete()
        else:
            job.save(update_fields=["video_name", "poster_name", "updated_at"])
        removed += 1
    return removed
