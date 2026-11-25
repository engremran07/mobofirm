"""Media: share cards (Pillow), ffmpeg slideshow, needs-media states, retention."""

from __future__ import annotations

import hashlib
import io
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image

from apps.distribution import media, services
from apps.distribution.content import Content
from apps.distribution.models import MediaAsset, ShareJob, ShareLog

pytestmark = pytest.mark.django_db


def spec(ref="official:1", **extra):
    base = {
        "kind": "firmware",
        "ref": ref,
        "title": "Acme Phone One Global",
        "label": "Firmware",
        "lines": ("Acme Phone One", "Android 14", "Build B123"),
        "site": "Mobofirm",
    }
    return media.MediaSpec(**{**base, **extra})


def asset_of(result: media.AssetResult) -> MediaAsset:
    assert result.asset is not None, result.reason
    return result.asset


def read_image(asset):
    with default_storage.open(asset.storage_name, "rb") as handle:
        return Image.open(io.BytesIO(handle.read()))


class MemoryStorage:
    """Minimal storage for replacement failure tests."""

    def __init__(self, *, missing_first=False):
        """Start with no files."""
        self.files = {}
        self.missing_first = missing_first

    def exists(self, name):
        """Report whether a path is present."""
        if self.missing_first:
            self.missing_first = False
            return False
        return name in self.files

    def save(self, name, content):
        """Store the uploaded bytes under their requested path."""
        self.files[name] = content.read()
        return name

    def delete(self, name):
        """Remove a stored path if present."""
        self.files.pop(name, None)


def fake_ffmpeg(monkeypatch, *, returncode=0, stderr=b""):
    monkeypatch.setattr(media, "ffmpeg_path", lambda: "ffmpeg")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if returncode == 0:
            Path(command[-1]).write_bytes(b"MP4-BYTES")
        return subprocess.CompletedProcess(command, returncode, b"", stderr)

    monkeypatch.setattr(media.subprocess, "run", run)
    return calls


def test_failed_asset_replacement_preserves_the_previous_file_and_row(monkeypatch):
    """A failed new write must leave the current asset and file untouched."""
    item = spec()
    digest = hashlib.sha256(f"{item.kind}:{item.ref}".encode()).hexdigest()[:20]
    old_name = f"distribution/card/{digest}.png"
    storage = MemoryStorage(missing_first=True)
    storage.files[old_name] = b"previous image"
    asset = MediaAsset.objects.create(
        content_kind=item.kind,
        object_ref=item.ref,
        asset_kind=MediaAsset.AssetKind.CARD,
        storage_name=old_name,
    )

    def fail_save(_name, _content):
        raise OSError("storage unavailable")

    monkeypatch.setattr(media, "render_card", lambda *_args: b"replacement image")
    monkeypatch.setattr(storage, "save", fail_save)
    monkeypatch.setattr(media, "default_storage", storage)

    with pytest.raises(OSError, match="storage unavailable"):
        media.ensure_card(item)

    asset.refresh_from_db()
    assert asset.storage_name == old_name
    assert storage.files[old_name] == b"previous image"


def test_successful_asset_replacement_deletes_old_file_after_commit(
    monkeypatch, django_capture_on_commit_callbacks
):
    """Retire the old media path only after the database replacement commits."""
    item = spec()
    digest = hashlib.sha256(f"{item.kind}:{item.ref}".encode()).hexdigest()[:20]
    old_name = f"distribution/card/{digest}.png"
    storage = MemoryStorage(missing_first=True)
    storage.files[old_name] = b"previous image"
    MediaAsset.objects.create(
        content_kind=item.kind,
        object_ref=item.ref,
        asset_kind=MediaAsset.AssetKind.CARD,
        storage_name=old_name,
    )
    monkeypatch.setattr(media, "default_storage", storage)
    monkeypatch.setattr(media, "render_card", lambda *_args: b"replacement image")

    with django_capture_on_commit_callbacks(execute=True):
        result = media.ensure_card(item)

    assert result.asset is not None
    assert result.asset.storage_name != old_name
    assert old_name not in storage.files
    assert storage.files[result.asset.storage_name] == b"replacement image"


def test_recreated_asset_starts_a_fresh_retention_window(
    monkeypatch, django_capture_on_commit_callbacks
):
    item = spec()
    now = timezone.now()
    old_created_at = now - timedelta(days=45)
    old_name = f"distribution/card/{hashlib.sha256(f'{item.kind}:{item.ref}'.encode()).hexdigest()[:20]}.png"
    storage = MemoryStorage(missing_first=True)
    storage.files[old_name] = b"previous image"
    asset = MediaAsset.objects.create(
        content_kind=item.kind,
        object_ref=item.ref,
        asset_kind=MediaAsset.AssetKind.CARD,
        storage_name=old_name,
    )
    MediaAsset.objects.filter(pk=asset.pk).update(created_at=old_created_at)
    monkeypatch.setattr(media, "default_storage", storage)
    monkeypatch.setattr(media, "render_card", lambda *_args: b"replacement image")

    with django_capture_on_commit_callbacks(execute=True):
        result = media.ensure_card(item)

    assert result.asset is not None
    result.asset.refresh_from_db()
    assert result.asset.created_at > old_created_at
    assert media.purge_expired(30, now=now) == 0
    assert storage.exists(result.asset.storage_name)


def test_cards_are_drawn_in_both_sizes_and_stored_once():
    card = media.ensure_card(spec())
    vertical = media.ensure_card(spec(), vertical=True)
    card_asset, vertical_asset = asset_of(card), asset_of(vertical)
    assert card.reason == ""
    assert read_image(card_asset).size == (1200, 630)
    assert read_image(vertical_asset).size == (1080, 1920)
    assert card_asset.storage_name.startswith("distribution/card/")
    assert media.ensure_card(spec()).asset == card_asset
    assert MediaAsset.objects.count() == 2


def test_the_card_url_is_public_and_absolute():
    media.ensure_card(spec())
    refs = media.refs_for("firmware", "official:1")
    assert refs.image_url.startswith(
        "https://mobofirm.example/media/distribution/card/"
    )
    assert refs.video_url == "" and refs.video_name == ""


def test_without_pillow_the_status_says_why_and_nothing_is_stored(monkeypatch):
    monkeypatch.setattr(media, "pillow_available", lambda: False)
    result = media.ensure_card(spec())
    assert result.asset is None and "Pillow" in result.reason
    assert not MediaAsset.objects.exists()
    assert media.capabilities()["cards"] is False


def test_without_ffmpeg_no_video_is_faked(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    result = media.ensure_video(spec())
    assert result.asset is None and "ffmpeg" in result.reason
    assert not MediaAsset.objects.exists()
    caps = media.capabilities()
    assert caps["video"] is False and "ffmpeg" in caps["video_note"]


def test_with_ffmpeg_a_vertical_slideshow_is_built_from_three_frames(monkeypatch):
    calls = fake_ffmpeg(monkeypatch)
    result = media.ensure_video(spec())
    video = asset_of(result)
    assert video.storage_name.endswith(".mp4")
    (command,) = calls
    assert command[0] == "ffmpeg" and "libx264" in command
    assert (
        "frame_%d.png" in " ".join(command)
        and "scale=1080:1920,format=yuv420p" in command
    )
    with default_storage.open(video.storage_name, "rb") as handle:
        assert handle.read() == b"MP4-BYTES"
    assert media.ensure_video(spec()).asset == video and len(calls) == 1
    assert media.refs_for("firmware", "official:1").video_name == video.storage_name


def test_a_failing_ffmpeg_reports_the_error_and_stores_nothing(monkeypatch):
    fake_ffmpeg(monkeypatch, returncode=1, stderr=b"boom: bad codec")
    result = media.ensure_video(spec())
    assert result.asset is None and "ffmpeg failed: boom: bad codec" in result.reason
    assert not MediaAsset.objects.exists()


def test_specs_show_only_public_facts():
    firmware = Content(
        kind="firmware",
        object_ref="official:1",
        title="Acme One",
        url="https://x/",
        brand="Acme",
        model="One",
        android_version="14",
        build_number="B1",
        chipset="Mediatek Helio G100 Ultra (6nm)",
    )
    lines = media.spec_from_content(firmware, "Mobofirm").lines
    assert lines == ("Acme One", "Android 14", "Build B1", "Mediatek Helio G100")
    blog = Content(
        kind="blog",
        object_ref="post:1",
        title="Teaser",
        url="https://x/",
        summary="s" * 300,
        gated=True,
    )
    assert len(media.spec_from_content(blog, "Mobofirm").lines[0]) <= 140


# ----------------------------------------------------------- pipeline ----


def test_an_image_platform_gets_its_card_generated_before_sending(
    make_channel, make_content, net
):
    make_channel("pinterest", use_composer=True)
    services.enqueue(make_content())
    fetcher = net.Fetcher(net.ok({"id": "pin1"}, status=201))
    assert services.process_due_jobs(fetcher=fetcher).sent == 1
    body = fetcher.bodies()[0]
    assert body["media_source"]["url"].startswith(
        "https://mobofirm.example/media/distribution/card/"
    )
    assert MediaAsset.objects.filter(asset_kind="card").count() == 1


def test_optional_images_are_attached_only_for_composer_channels(
    make_channel, make_content, net
):
    make_channel("telegram", use_composer=True)
    services.enqueue(make_content())
    fetcher = net.Fetcher(net.ok({"ok": True, "result": {"message_id": 1}}))
    services.process_due_jobs(fetcher=fetcher)
    assert fetcher.calls[0]["url"].endswith("/sendPhoto")


def test_a_job_that_needs_media_waits_without_spending_an_attempt(
    make_channel, make_content, net, monkeypatch
):
    make_channel("pinterest", use_composer=True)
    monkeypatch.setattr(media, "pillow_available", lambda: False)
    services.enqueue(make_content())
    now = timezone.now()
    fetcher = net.Fetcher()
    summary = services.process_due_jobs(now=now, fetcher=fetcher)
    job = ShareJob.objects.get()
    assert summary.sent == 0 and fetcher.calls == []
    assert job.status == ShareJob.Status.NEEDS_MEDIA and job.attempts == 0
    assert "image" in job.last_error.lower() and "Pillow" in job.last_error
    assert job.next_attempt_at == now + timedelta(minutes=30)
    assert (
        ShareLog.objects.filter(
            job=job, message__startswith="Waiting for media"
        ).count()
        == 1
    )
    assert (
        services.process_due_jobs(now=now + timedelta(minutes=5), fetcher=fetcher).sent
        == 0
    )
    monkeypatch.undo()
    later = now + timedelta(minutes=31)
    sent = services.process_due_jobs(
        now=later, fetcher=net.Fetcher(net.ok({"id": "p"}, status=201))
    )
    assert sent.sent == 1
    job.refresh_from_db()
    assert job.status == ShareJob.Status.SENT and job.attempts == 1
    assert (
        ShareLog.objects.filter(
            job=job, message__startswith="Waiting for media"
        ).count()
        == 1
    )


def test_video_platforms_wait_for_an_approved_video_then_upload_it(
    make_channel, make_content, net
):
    from django.core.files.base import ContentFile

    from apps.distribution import videos
    from apps.distribution.models import VideoJob

    make_channel("youtube", use_composer=True)
    services.enqueue(make_content())
    now = timezone.now()
    fetcher = net.Fetcher()
    services.process_due_jobs(now=now, fetcher=fetcher)
    job = ShareJob.objects.get()
    assert job.status == "needs_media" and "approved motion video" in job.last_error
    assert fetcher.calls == []
    name = "distribution/videos/test-token/video.mp4"
    default_storage.save(name, ContentFile(b"MP4-BYTES"))
    video = VideoJob.objects.create(
        content_kind="firmware",
        object_ref="official:abc",
        title="Acme Phone One Global",
        template="firmware-release",
        idempotency_key="gate-test",
        status=VideoJob.Status.READY,
        video_name=name,
    )
    # A rendered video that nobody approved never unlocks posting.
    services.process_due_jobs(now=now + timedelta(minutes=31), fetcher=fetcher)
    assert fetcher.calls == []
    assert videos.approve(video, None, now=now + timedelta(minutes=32))
    upload = net.Fetcher(net.ok({"access_token": "yt-access"}), net.ok({"id": "vid9"}))
    assert (
        services.process_due_jobs(now=now + timedelta(minutes=33), fetcher=upload).sent
        == 1
    )
    assert b"MP4-BYTES" in upload.calls[1]["body"]
    job.refresh_from_db()
    video.refresh_from_db()
    assert job.external_id == "vid9" and video.status == VideoJob.Status.POSTED


def test_a_job_that_waits_too_long_for_media_is_dropped_visibly(
    make_channel, make_content, net, monkeypatch
):
    make_channel("tiktok", use_composer=True)
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    services.enqueue(make_content())
    ShareJob.objects.update(created_at=timezone.now() - timedelta(days=8))
    services.process_due_jobs(fetcher=net.Fetcher())
    job = ShareJob.objects.get()
    assert job.status == ShareJob.Status.SKIPPED and "approved" in job.last_error
    assert ShareLog.objects.filter(job=job, message__startswith="Dropped").exists()


def test_a_test_post_to_a_video_platform_fails_clearly_without_ffmpeg(
    make_channel, net, monkeypatch
):
    channel = make_channel("youtube")
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    fetcher = net.Fetcher()
    job = services.send_test(channel, fetcher=fetcher)
    assert job.status == "failed" and "video" in job.last_error.lower()
    assert fetcher.calls == []


def test_purge_removes_expired_files_and_keeps_fresh_ones():
    old = asset_of(media.ensure_card(spec("official:old")))
    fresh = asset_of(media.ensure_card(spec("official:fresh")))
    MediaAsset.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=45)
    )
    assert media.purge_expired(30) == 1
    assert not default_storage.exists(old.storage_name)
    assert default_storage.exists(fresh.storage_name)
    assert media.purge_expired(0) == 0
