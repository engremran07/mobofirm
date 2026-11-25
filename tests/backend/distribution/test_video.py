"""Motion video: plans, template packs, escaping, safe zones, the state machine, the approval
gate, the daily cap, idempotency, timeouts, retention and the staff Videos page.

The renderer's real tools (headless Chromium and ffmpeg) are an explicit requirement of this
suite: ``test_environment_requirements`` and the golden clip FAIL, loudly and with install
guidance, when they are missing. Nothing here skips.
"""

from __future__ import annotations

import io
import json
import subprocess
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.distribution import videos
from apps.distribution.content import Content
from apps.distribution.models import DistributionSettings, ShareJob, VideoJob
from apps.distribution.video import audio, capabilities, packs, plan, render, sources
from apps.distribution.video.plan import Brand, PlanError

pytestmark = pytest.mark.django_db
BRAND = Brand("Mobofirm", "mobofirm.example")
HOSTILE = "</script><img src=x onerror=alert(1)> & <b>bold</b>"


def firmware_source(**extra) -> sources.Source:
    base = {
        "kind": "firmware",
        "ref": "official:1",
        "title": "Northstar Atlas One Global",
        "url": "https://mobofirm.example/firmwares/file/official/1/",
        "brand": "Northstar",
        "model": "Atlas One",
        "variant": "Global",
        "chipset": "Snapdragon 8 Gen 3",
        "android_version": "14",
        "build_number": "UP1A.231005.007",
        "size_bytes": 3_412_000_000,
    }
    return sources.Source(**{**base, **extra})


def guide_source(**extra) -> sources.Source:
    base = {
        "kind": "blog",
        "ref": "post:2",
        "title": "How to flash a Mediatek phone with SP Flash Tool",
        "url": "https://mobofirm.example/blog/flash/",
        "summary": "A careful walk through scatter files and USB drivers.",
        "chipset": "MT6789",
        "is_guide": True,
        "reading_minutes": 6,
        "tags": ("Mediatek", "Guide"),
        "steps": (
            "Install the USB VCOM drivers",
            "Open SP Flash Tool and load the scatter file",
            "Select Download Only and click Download",
            "Connect the powered-off phone",
            "Wait for the green check",
        ),
        "points": ("USB drivers", "Scatter file"),
    }
    return sources.Source(**{**base, **extra})


def roundup_source() -> sources.Source:
    return sources.Source(
        "roundup",
        "week:2026-W47",
        "This week's firmware",
        "https://mobofirm.example/",
        summary="12 new verified firmware",
        items=(
            ("Northstar Atlas One", "Snapdragon"),
            ("Acme Phone", "MT6789"),
            ("Zed Z9", ""),
            ("Orbit O1", ""),
        ),
        bars=(("Northstar", 5), ("Acme", 4), ("Zed", 3)),
    )


def all_cases():
    return [
        (packs.PACKS["firmware-release"], firmware_source()),
        (packs.PACKS["flashing-guide"], guide_source()),
        (
            packs.PACKS["blog-post"],
            guide_source(is_guide=False, steps=(), points=("One idea", "Another idea")),
        ),
        (packs.PACKS["weekly-roundup"], roundup_source()),
        (
            packs.PACKS["spotlight"],
            sources.Source(
                "spotlight",
                "brand:acme",
                "Acme spotlight",
                "https://mobofirm.example/",
                summary="6 verified firmware",
                brand="Acme",
                items=(("Acme One", ""), ("Acme Two", "")),
                bars=(("Firmware", 6),),
            ),
        ),
    ]


def build(pack, source, *, preset="9x16", hook="A", **extra):
    return plan.build_plan(
        pack.build(source, hook, BRAND),
        preset=preset,
        brand=BRAND,
        min_ms=pack.min_ms,
        max_ms=pack.max_ms,
        **extra,
    )


# ------------------------------------------------------------ environment ----


def test_environment_requirements():
    """The renderer's required tools. This fails (never skips) when one is missing."""
    missing = [
        f"{c.label}: {c.guidance}"
        for c in capabilities.report()
        if c.required and not c.ok
    ]
    assert not missing, "Motion video needs these tools:\n" + "\n".join(missing)


def test_capability_report_lists_every_tool_with_guidance():
    report = {c.key: c for c in capabilities.report()}
    assert set(report) == {"ffmpeg", "chromium", "tts", "fonts"}
    assert (
        report["ffmpeg"].required
        and report["chromium"].required
        and not report["tts"].required
    )
    assert "winget install Gyan.FFmpeg" in capabilities.INSTALL_FFMPEG
    assert "playwright install chromium" in capabilities.INSTALL_CHROMIUM


def test_voice_is_optional_and_never_cloud(monkeypatch):
    monkeypatch.setattr(capabilities, "espeak_path", lambda: "")
    monkeypatch.setattr(capabilities, "piper_path", lambda: ("", ""))
    assert capabilities.tts_engine() == ""
    assert audio.synthesize_voice([("a", "hello")], Path(".")) == []


# ------------------------------------------------------------------- plans ----


@pytest.mark.parametrize("preset", sorted(plan.PRESETS))
def test_every_pack_fits_the_duration_rule_in_every_preset(preset):
    for pack, source in all_cases():
        for hook in ("A", "B"):
            built = build(pack, source, preset=preset, hook=hook)
            assert plan.MIN_MS <= built["total"] <= plan.MAX_MS, (
                pack.slug,
                hook,
                built["total"],
            )
            assert built["w"] == plan.PRESETS[preset].width and built["fps"] == 30
            scenes = built["scenes"]
            assert scenes[0]["start"] == 0 and scenes[-1]["kind"] == "cta"
            assert all(a["end"] > b["start"] for a, b in pairwise(scenes))  # overlap
            beats = built["beats"]
            assert all(b["end"] > b["start"] for b in beats)
            assert beats[-1]["end"] <= built["total"]


def test_hook_variants_differ_and_templates_are_versioned():
    one = build(packs.PACKS["firmware-release"], firmware_source(), hook="A")
    two = build(packs.PACKS["firmware-release"], firmware_source(), hook="B")
    assert one["beats"][0]["text"] != two["beats"][0]["text"]
    assert all(p.version >= 1 for p in packs.PACKS.values())
    assert videos.key_for(
        "firmware", "official:1", packs.PACKS["firmware-release"], "9x16", "A"
    ) != videos.key_for(
        "firmware", "official:1", packs.PACKS["firmware-release"], "9x16", "B"
    )


def test_guide_steps_appear_on_the_beat_that_narrates_them():
    built = build(packs.PACKS["flashing-guide"], guide_source())
    steps = next(s for s in built["scenes"] if s["kind"] == "steps")
    assert len(steps["data"]["steps"]) == 5 and len(steps["data"]["at"]) == 5
    assert (
        steps["data"]["at"] == sorted(steps["data"]["at"])
        and steps["data"]["at"][0] >= 0
    )
    assert "sync" not in steps["data"]


def test_too_short_and_too_long_plans_are_refused():
    tiny = plan.Draft(
        (plan.Beat("a", "Hi there."),),
        (plan.SceneSpec("s", "hook", {"headline": "x", "kicker": "k"}, ("a",)),),
    )
    padded = plan.build_plan(
        tiny, preset="9x16", brand=BRAND
    )  # padded up to the minimum
    assert padded["total"] >= plan.MIN_MS
    beats = tuple(plan.Beat(f"b{i}", "word " * 30) for i in range(12))
    scenes = tuple(
        plan.SceneSpec(f"s{i}", "hook", {"headline": "x", "kicker": "k"}, (f"b{i}",))
        for i in range(12)
    )
    with pytest.raises(PlanError, match="limit is"):
        plan.build_plan(plan.Draft(beats, scenes), preset="9x16", brand=BRAND)


def test_optional_beats_are_dropped_before_a_video_runs_long():
    beats = [plan.Beat(f"b{i}", "word " * 18, optional=i > 3) for i in range(10)]
    scenes = [
        plan.SceneSpec(f"s{i}", "hook", {"headline": "x", "kicker": "k"}, (f"b{i}",))
        for i in range(10)
    ]
    built = plan.build_plan(
        plan.Draft(tuple(beats), tuple(scenes)), preset="9x16", brand=BRAND
    )
    assert built["total"] <= plan.MAX_MS and len(built["beats"]) < 10


def test_template_parameters_are_validated():
    base = packs.PACKS["firmware-release"].build(firmware_source(), "A", BRAND)
    with pytest.raises(PlanError, match="Unknown preset"):
        plan.build_plan(base, preset="4x3", brand=BRAND)
    with pytest.raises(PlanError, match="Brand colour"):
        plan.build_plan(base, preset="9x16", brand=Brand("x", "y", {"accent": "red"}))
    bad_kind = plan.Draft(base.beats, (plan.SceneSpec("s", "explode", {}, ()),))
    with pytest.raises(PlanError, match="Unknown scene kind"):
        plan.build_plan(bad_kind, preset="9x16", brand=BRAND)
    dup = plan.Draft((plan.Beat("a", "x y"), plan.Beat("a", "x z")), base.scenes)
    with pytest.raises(PlanError, match="unique"):
        plan.build_plan(dup, preset="9x16", brand=BRAND)
    long_beat = plan.Draft(
        (plan.Beat("a", "x" * 400),), (plan.SceneSpec("s", "hook", {}, ("a",)),)
    )
    with pytest.raises(PlanError, match="characters"):
        plan.build_plan(long_beat, preset="9x16", brand=BRAND)


def test_brand_tokens_match_the_console_palette():
    palette = (
        Path(__file__).resolve().parents[3] / "frontend/src/shared/tokens/_palette.scss"
    ).read_text(encoding="utf-8")
    assert (
        plan.BRAND_COLORS["accent"] in palette
        and plan.BRAND_COLORS["accent2"] in palette
    )


# ---------------------------------------------------------------- escaping ----


def test_plan_json_cannot_close_a_script_block_or_inject_markup():
    source = firmware_source(title=HOSTILE, brand=HOSTILE, model=HOSTILE)
    built = build(packs.PACKS["firmware-release"], source)
    html = plan.page_html(built)
    assert (
        html.count("</script>") == 2
    )  # the plan block and the runtime, nothing smuggled in
    encoded = plan.plan_json(built)
    assert "<" not in encoded and ">" not in encoded and "&" not in encoded
    assert json.loads(encoded)["scenes"][1]["data"]["name"].startswith(
        "</script>"
    )  # data intact
    assert (
        plan.CSP.startswith("default-src 'none'") and "connect-src 'none'" in plan.CSP
    )


def test_the_render_page_treats_hostile_text_as_text():
    ready, detail = capabilities.chromium_ready()
    assert ready, detail
    from apps.distribution.video.render import ChromiumFrames

    built = build(
        packs.PACKS["firmware-release"],
        firmware_source(title=HOSTILE, brand=HOSTILE, model="Atlas"),
    )
    with ChromiumFrames(built) as frames:
        tab = frames.page
        tab.evaluate("t => window.__seek(t)", built["scenes"][0]["start"] + 1500)
        assert (
            tab.evaluate(
                "document.querySelectorAll('img, iframe, object, embed, link, style script').length"
            )
            == 0
        )
        assert tab.evaluate("window.__injected === undefined")
        assert " ".join(HOSTILE.split()) in " ".join(tab.inner_text("#stage").split())
        requests: list[str] = []
        tab.on("request", lambda r: requests.append(r.url))
        tab.evaluate("t => window.__seek(t)", 2500)
        assert requests == []  # nothing is ever fetched


def test_local_images_are_verified_and_remote_ones_ignored():
    from apps.distribution.video import art

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(buffer, "PNG")
    default_storage.save("devices/a.png", ContentFile(buffer.getvalue()))
    default_storage.save("devices/bad.png", ContentFile(b"<script>alert(1)</script>"))
    assert art.local_image("/media/devices/a.png").startswith("data:image/jpeg;base64,")
    assert art.local_image("/media/devices/bad.png") == ""
    assert art.local_image("https://evil.example/x.png") == ""  # never hotlinked
    assert art.local_image("/media/../etc/passwd") == ""
    qr_size = art.qr_path("https://mobofirm.example/x")["size"]
    assert isinstance(qr_size, int) and qr_size >= 21


def test_encoded_path_traversal_is_rejected_before_storage_read():
    """Reject URL-encoded traversal before asking storage to open the path."""
    from apps.distribution.video import art

    assert not art.local_image("/media/%2e%2e/etc/passwd")


def test_oversized_local_images_are_rejected_before_decode(monkeypatch, tmp_path):
    """Reject images over the pixel budget before Pillow decodes them."""
    from apps.distribution.video import art

    monkeypatch.setattr(art, "MAX_IMAGE_PIXELS", 100)
    buffer = io.BytesIO()
    Image.new("RGB", (11, 10), "red").save(buffer, "PNG")
    name = default_storage.save(
        f"devices/{tmp_path.name}.png", ContentFile(buffer.getvalue())
    )

    assert not art.local_image(f"/media/{name}")


# --------------------------------------------------------------- safe zones ----

BOXES = ".headline, .title, .kicker, .sub, .stat, .badge, .qr, .button, .site, .warn, .device-name, .lower-third, .point, .step, .bar-row, .device-stage"


@pytest.mark.parametrize("preset", sorted(plan.PRESETS))
def test_text_stays_inside_the_platform_safe_zones(preset):
    from apps.distribution.video.render import ChromiumFrames

    for pack, source in all_cases():
        built = build(pack, source, preset=preset)
        safe, width, height = built["safe"], built["w"], built["h"]
        with ChromiumFrames(built) as frames:
            tab = frames.page
            for scene in built["scenes"]:
                at = scene["start"] + (scene["end"] - scene["start"]) * 0.6
                tab.evaluate("t => window.__seek(t)", at)
                boxes = tab.evaluate(
                    f"""() => [...document.querySelectorAll('.scene:not([style*="display: none"]) :is({BOXES})')]
                        .filter(e => getComputedStyle(e).opacity > 0.9 && !e.classList.contains('float'))
                        .map(e => {{ const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom, e.className]; }})"""
                )
                for left, top, right, bottom, name in boxes:
                    where = (pack.slug, preset, scene["id"], name)
                    assert left >= safe["l"] - 6 and right <= width - safe["r"] + 6, (
                        where
                    )
                    assert top >= safe["t"] - 6 and bottom <= height - safe["b"] + 6, (
                        where
                    )
            captions = tab.evaluate(
                "(() => { const r = document.querySelector('.captions').getBoundingClientRect(); return [r.left, r.right, r.bottom]; })()"
            )
            assert captions[0] >= safe["l"] - 6 and captions[1] <= width - safe["r"] + 6
            assert captions[2] <= height - safe["b"] + 6


# ------------------------------------------------------------ golden render ----


def ffprobe(path: Path) -> dict:
    ffmpeg = Path(capabilities.ffmpeg_path())
    binary = str(ffmpeg.with_name("ffprobe" + ffmpeg.suffix))
    run = subprocess.run(
        [
            binary,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,r_frame_rate,nb_frames:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(run.stdout)


def loudness(path: Path) -> float:
    run = subprocess.run(
        [
            capabilities.ffmpeg_path(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "ebur128",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    summary = run.stderr.rsplit("Summary:", 1)[-1]
    line = next(line for line in summary.splitlines() if line.strip().startswith("I:"))
    return float(line.split()[1])


def test_golden_clip_renders_a_valid_h264_video_with_normalised_audio(tmp_path):
    """A real 3-second render: Chromium frames, procedural music, ffmpeg H.264 + AAC."""
    pack = packs.PACKS["smoke"]
    built = plan.build_plan(
        pack.build(firmware_source(), "A", BRAND),
        preset="9x16",
        brand=BRAND,
        beat_ms={"a": 900, "b": 900},
        min_ms=pack.min_ms,
        max_ms=pack.max_ms,
    )
    assert 2800 <= built["total"] <= 3600
    result = render.render_video(built, tmp_path, timeout_s=240, workers=2)
    probe = ffprobe(result.path)
    video = next(s for s in probe["streams"] if s["codec_name"] == "h264")
    sound = next(s for s in probe["streams"] if s["codec_name"] == "aac")
    assert (
        video["width"],
        video["height"],
        video["pix_fmt"],
        video["r_frame_rate"],
    ) == (1080, 1920, "yuv420p", "30/1")
    assert sound["codec_name"] == "aac"
    assert abs(float(probe["format"]["duration"]) - built["total"] / 1000) < 0.2
    assert result.frames == round(built["total"] / 1000 * 30)
    assert -15.5 <= loudness(result.path) <= -12.5  # -14 LUFS target
    assert result.poster[:2] == b"\xff\xd8" and result.size_bytes > 20_000
    assert (
        result.path.read_bytes()[4:8] == b"ftyp"
        and b"moov" in result.path.read_bytes()[:4096]
    )  # faststart


def test_frames_are_deterministic():
    from apps.distribution.video.render import ChromiumFrames

    built = build(packs.PACKS["firmware-release"], firmware_source())
    with ChromiumFrames(built) as first:
        a = first.frame(5200)
    with ChromiumFrames(built) as second:
        b = second.frame(5200)
    assert a == b


def test_procedural_music_has_the_requested_length_and_a_recorded_licence(tmp_path):
    import wave

    path = audio.write_bed(tmp_path / "bed.wav", 2000, 3, [0, 1000])
    with wave.open(str(path), "rb") as handle:
        assert abs(handle.getnframes() / handle.getframerate() - 2.0) < 0.01
    assert "no third-party audio" in audio.LICENCE


def test_encoder_command_follows_the_platform_recipe(tmp_path):
    built = build(packs.PACKS["firmware-release"], firmware_source())
    command = render.FfmpegEncoder("ffmpeg").command(
        built, tmp_path, tmp_path / "f", tmp_path / "a.wav"
    )
    joined = " ".join(command)
    for token in (
        "libx264",
        "yuv420p",
        "+faststart",
        "scale=1080:1920",
        "aac",
        "bt709",
    ):
        assert token in joined
    assert "-an" in " ".join(
        render.FfmpegEncoder("ffmpeg").command(built, tmp_path, tmp_path / "f", None)
    )


# ---------------------------------------------------------- pipeline (stubs) ----


class StubFrames:
    def __init__(self, _plan, delay=0.0):
        self.delay = delay
        buffer = io.BytesIO()
        Image.new("RGB", (16, 16), "teal").save(buffer, "JPEG")
        self.jpeg = buffer.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def frame(self, _ms):
        import time

        time.sleep(self.delay)
        return self.jpeg


class StubEncoder:
    def __init__(self):
        self.calls = 0

    def encode(self, _plan, work, _frames, _soundtrack, _deadline):
        self.calls += 1
        out = work / "out.mp4"
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"x" * 4096)
        return out


def new_draft(**extra):
    job, reason = videos.create(
        "firmware", "official:1", source=firmware_source(), **extra
    )
    assert job is not None, reason
    return job


def queued(job):
    assert videos.request_render(job, dispatch=lambda pk: None)
    job.refresh_from_db()
    return job


def render_stubbed(job, **extra):
    options = {
        "frames_factory": StubFrames,
        "encoder": StubEncoder(),
        "workers": 1,
        "voice": False,
        "with_audio": False,
    }
    return videos.render_job(job.pk, **{**options, **extra})


class MemoryStorage:
    """Small storage fake supporting failed writes and deletion assertions."""

    def __init__(self, files=None, *, fail_on=""):
        """Initialize stored files and an optional failing filename suffix."""
        self.files = dict(files or {})
        self.fail_on = fail_on

    def exists(self, name):
        """Report whether a path is present."""
        return name in self.files

    def save(self, name, content):
        """Store bytes unless this path is configured to fail."""
        if self.fail_on and name.endswith(self.fail_on):
            raise OSError("storage unavailable")
        self.files[name] = content.read()
        return name

    def delete(self, name):
        """Remove a stored path if present."""
        self.files.pop(name, None)


def test_creation_is_idempotent_and_validates_content():
    first = new_draft()
    again, reason = videos.create("firmware", "official:1", source=firmware_source())
    assert again == first and "already exists" in reason
    assert VideoJob.objects.count() == 1
    other = new_draft(hook="B")
    assert other != first and VideoJob.objects.count() == 2
    assert (
        videos.create("firmware", "official:1", hook="C", source=firmware_source())[0]
        is None
    )
    assert (
        videos.create(
            "firmware", "official:1", pack_slug="smoke", source=firmware_source()
        )[0]
        is None
    )
    assert (
        videos.create(
            "firmware",
            "official:1",
            pack_slug="weekly-roundup",
            source=firmware_source(),
        )[0]
        is None
    )
    assert videos.create("firmware", "official:404")[0] is None  # not public


def test_state_machine_draft_to_ready_to_approved_to_posted():
    job = queued(new_draft())
    assert job.status == VideoJob.Status.RENDERING
    assert not videos.request_render(job, dispatch=lambda pk: None)  # already queued
    assert render_stubbed(job) == "ready"
    job.refresh_from_db()
    assert (
        job.status == "ready"
        and job.has_file
        and job.size_bytes > 1000
        and len(job.checksum) == 64
    )
    assert default_storage.exists(job.video_name) and job.media_token in job.video_name
    assert videos.approve(job, None)
    assert not videos.approve(job, None)  # idempotent
    videos.note_posted("firmware", "official:1")
    job.refresh_from_db()
    assert job.status == "posted"


def test_failed_render_replacement_keeps_previous_video_and_poster(
    tmp_path, monkeypatch
):
    """A poster write failure must preserve the prior video and poster."""
    job = queued(new_draft())
    old_video = "distribution/videos/old-token/video.mp4"
    old_poster = "distribution/videos/old-token/poster.jpg"
    job.media_token = "old-token"
    job.video_name = old_video
    job.poster_name = old_poster
    job.save(update_fields=["media_token", "video_name", "poster_name"])
    storage = MemoryStorage(
        {old_video: b"previous video", old_poster: b"previous poster"},
        fail_on="poster.jpg",
    )
    monkeypatch.setattr(videos, "default_storage", storage)
    rendered = tmp_path / "replacement.mp4"
    rendered.write_bytes(b"replacement video")
    result = render.RenderResult(
        rendered, b"replacement poster", 1000, 18, "a" * 64, False
    )
    monkeypatch.setattr(videos.render, "render_video", lambda *args, **kwargs: result)

    assert videos.render_job(job.pk, voice=False) == "failed"

    job.refresh_from_db()
    assert job.video_name == old_video
    assert job.poster_name == old_poster
    assert storage.files == {
        old_video: b"previous video",
        old_poster: b"previous poster",
    }


def test_successful_render_replacement_removes_old_files_after_commit(
    tmp_path, monkeypatch, django_capture_on_commit_callbacks
):
    """Retire old render files after the new video metadata commits."""
    job = queued(new_draft())
    old_video = "distribution/videos/old-token/video.mp4"
    old_poster = "distribution/videos/old-token/poster.jpg"
    job.media_token = "old-token"
    job.video_name = old_video
    job.poster_name = old_poster
    job.save(update_fields=["media_token", "video_name", "poster_name"])
    storage = MemoryStorage(
        {old_video: b"previous video", old_poster: b"previous poster"}
    )
    monkeypatch.setattr(videos, "default_storage", storage)
    rendered = tmp_path / "replacement.mp4"
    rendered.write_bytes(b"replacement video")
    result = render.RenderResult(
        rendered, b"replacement poster", 1000, 18, "a" * 64, False
    )
    monkeypatch.setattr(videos.render, "render_video", lambda *args, **kwargs: result)

    with django_capture_on_commit_callbacks(execute=True):
        assert videos.render_job(job.pk, voice=False) == "ready"

    job.refresh_from_db()
    assert job.video_name != old_video
    assert job.poster_name != old_poster
    assert old_video not in storage.files
    assert old_poster not in storage.files
    assert storage.files[job.video_name] == b"replacement video"
    assert storage.files[job.poster_name] == b"replacement poster"


def test_a_second_worker_cannot_render_the_same_job():
    job = queued(new_draft())
    assert render_stubbed(job) == "ready"
    assert render_stubbed(job) == ""  # nothing left to claim


def test_nothing_is_postable_without_approval():
    job = queued(new_draft())
    render_stubbed(job)
    job.refresh_from_db()
    assert videos.approved_refs("firmware", "official:1") is None  # ready is not enough
    videos.reject(job, None, "not good enough")
    job.refresh_from_db()
    assert job.status == "rejected" and job.decision_note
    assert videos.approved_refs("firmware", "official:1") is None
    assert not videos.approve(job, None)  # a rejected video cannot be approved
    fresh, _ = videos.rerender(job, None, hook="B", dispatch=lambda pk: None)
    assert fresh is not None and fresh != job and fresh.status == "rendering"
    assert videos.approved_refs("firmware", "official:1") is None


def test_approve_requires_a_file():
    job = new_draft()
    VideoJob.objects.filter(pk=job.pk).update(status="ready")
    job.refresh_from_db()
    assert not videos.approve(job, None)


def test_approving_releases_waiting_posts():
    from apps.distribution.models import Channel

    channel = Channel.objects.create(name="tt", kind="tiktok", enabled=False)
    waiting = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref="official:1",
        idempotency_key="w1",
        title="t",
        url="https://x.example/",
        text="t",
        status=ShareJob.Status.NEEDS_MEDIA,
        next_attempt_at=timezone.now() + timedelta(hours=1),
    )
    job = queued(new_draft())
    render_stubbed(job)
    job.refresh_from_db()
    videos.approve(job, None)
    waiting.refresh_from_db()
    assert waiting.next_attempt_at <= timezone.now() + timedelta(seconds=5)


def test_the_render_deadline_kills_a_slow_renderer(tmp_path):
    built = build(packs.PACKS["firmware-release"], firmware_source())

    def slow(plan_data):
        return StubFrames(plan_data, delay=0.2)

    with pytest.raises(render.RenderError, match="exceeded") as info:
        render.render_video(
            built,
            tmp_path,
            timeout_s=0.2,
            frames_factory=slow,
            encoder=StubEncoder(),
            workers=1,
            with_audio=False,
        )
    assert info.value.retryable


def test_parallel_workers_are_killed_on_timeout(tmp_path, monkeypatch):
    killed: list[bool] = []

    class Hung:
        returncode = None
        stderr = None

        def poll(self):
            return self.returncode

        def kill(self):
            killed.append(True)
            self.returncode = -9

        def wait(self, timeout=None):
            del timeout
            return self.returncode

    monkeypatch.setattr(render.subprocess, "Popen", lambda *a, **k: Hung())
    built = build(packs.PACKS["firmware-release"], firmware_source())
    with pytest.raises(render.RenderError, match="exceeded"):
        render.render_video(
            built,
            tmp_path,
            timeout_s=0.5,
            workers=3,
            encoder=StubEncoder(),
            with_audio=False,
        )
    assert killed == [True, True, True]


def test_a_retryable_failure_requeues_and_a_final_one_stops(monkeypatch):
    job = queued(new_draft())
    original = render.render_video

    def tight(plan_data, out_dir, **kwargs):
        kwargs["timeout_s"] = 0.1
        return original(plan_data, out_dir, **kwargs)

    def always_slow(plan_data):
        return StubFrames(plan_data, delay=1.0)

    monkeypatch.setattr(videos.render, "render_video", tight)
    results = []
    for _ in range(videos.MAX_ATTEMPTS):
        results.append(
            videos.render_job(
                job.pk,
                frames_factory=always_slow,
                encoder=StubEncoder(),
                workers=1,
                voice=False,
                with_audio=False,
            )
        )
        job.refresh_from_db()
    assert results == ["retry", "retry", "failed"]
    assert (
        job.status == "failed"
        and job.attempts == videos.MAX_ATTEMPTS
        and "exceeded" in job.error
    )
    restarted, _ = videos.rerender(job, None, dispatch=lambda pk: None)
    assert restarted is not None
    assert restarted.attempts == 0  # staff can start over


def test_missing_ffmpeg_fails_clearly_without_posting(monkeypatch):
    monkeypatch.setattr(capabilities, "can_render", lambda: False)
    job = new_draft()
    assert not videos.request_render(job)
    job.refresh_from_db()
    assert job.status == "failed" and "capability report" in job.error


# ------------------------------------------------- enlisting, cap, retention ----


def test_publishing_enlists_one_draft_after_commit_and_never_twice(
    make_post, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        post = make_post("A brand new journal post about flashing")
    assert (
        VideoJob.objects.filter(
            content_kind="blog", object_ref=f"post:{post.pk}"
        ).count()
        == 1
    )
    job = VideoJob.objects.get()
    assert job.params["auto"] is True and job.status == "draft"
    item = Content(
        kind="blog",
        object_ref=f"post:{post.pk}",
        title=post.title,
        url="https://mobofirm.example/x/",
    )
    videos.enlist(item)
    assert VideoJob.objects.count() == 1


def test_a_bulk_import_cannot_flood_the_queue(
    make_post, django_capture_on_commit_callbacks
):
    DistributionSettings.objects.update_or_create(pk=1, defaults={"video_daily_cap": 3})
    with django_capture_on_commit_callbacks(execute=True):
        for index in range(12):
            make_post(f"Imported journal post number {index} about firmware")
    assert VideoJob.objects.filter(params__auto=True).count() == 3
    automatic = VideoJob.objects.first()
    assert automatic is not None
    manual, _ = videos.create(
        "blog",
        f"post:{automatic.object_ref.split(':')[1]}",
        pack_slug="blog-post",
        hook="B",
    )
    assert manual is not None  # staff are not limited by the automatic cap


def test_automatic_drafts_can_be_switched_off(
    make_post, django_capture_on_commit_callbacks
):
    DistributionSettings.objects.update_or_create(
        pk=1, defaults={"video_auto_enlist": False}
    )
    with django_capture_on_commit_callbacks(execute=True):
        make_post("A post that gets no automatic video")
    assert not VideoJob.objects.exists()


def test_retention_removes_old_files_but_keeps_approved_videos():
    old_ready, kept = queued(new_draft()), queued(new_draft(hook="B"))
    render_stubbed(old_ready)
    render_stubbed(kept)
    kept.refresh_from_db()
    videos.approve(kept, None)
    VideoJob.objects.update(created_at=timezone.now() - timedelta(days=90))
    old_ready.refresh_from_db()
    name = old_ready.video_name
    assert videos.purge_expired(30) == 1
    assert not default_storage.exists(name)
    kept.refresh_from_db()
    assert default_storage.exists(kept.video_name) and kept.status == "approved"
    assert videos.purge_expired(0) == 0


def test_stale_renders_are_failed_by_the_sweep():
    job = queued(new_draft())
    VideoJob.objects.filter(pk=job.pk).update(
        updated_at=timezone.now() - timedelta(hours=2)
    )
    assert videos.sweep_stale() == 1
    job.refresh_from_db()
    assert job.status == "failed" and "worker" in job.error


def test_weekly_roundup_is_created_once_per_week(monkeypatch):
    monkeypatch.setattr(
        sources,
        "load",
        lambda kind, ref: roundup_source() if kind == "roundup" else None,
    )
    first = videos.enlist_weekly_roundup()
    assert first is not None and first.content_kind == "roundup"
    second = videos.enlist_weekly_roundup()
    assert second is not None and second.pk == first.pk
    assert VideoJob.objects.count() == 1


def test_params_snapshot_round_trips():
    original = guide_source()
    job, _ = videos.create("blog", "post:2", source=original)
    assert job is not None
    assert videos.source_from(job.params) == original


# --------------------------------------------------------------- staff page ----


def videos_url():
    return reverse("admin:distribution_channel_videos")


def ready_job():
    job = queued(new_draft())
    render_stubbed(job)
    job.refresh_from_db()
    return job


def test_videos_page_shows_the_capability_report_queue_and_filters(admin_client):
    ready_job()
    response = admin_client.get(videos_url())
    body = response.content.decode()
    assert response.status_code == 200
    for text in (
        "Rendering tools",
        "Nothing is posted without your approval",
        "Approve",
        "Create a video",
        "Hook A",
    ):
        assert text in body
    assert "<video" in body and 'preload="none"' in body
    assert admin_client.get(videos_url(), {"status": "failed"}).context["jobs"] == []
    assert (
        len(admin_client.get(videos_url(), {"kind": "firmware"}).context["jobs"]) == 1
    )
    assert (
        admin_client.get(videos_url(), {"status": "bogus"}).context["filters"]["status"]
        == ""
    )


def test_live_region_polls_only_while_something_renders(admin_client):
    job = queued(new_draft())
    live = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "dist-videos-live"}
    busy = admin_client.get(videos_url(), **live).content.decode()
    assert (
        'id="dist-videos-live"' in busy
        and "<html" not in busy
        and 'hx-trigger="every 5s"' in busy
    )
    render_stubbed(job)
    idle = admin_client.get(videos_url(), **live).content.decode()
    assert "every 5s" not in idle


def test_approve_reject_and_bulk_follow_post_redirect_get(admin_client):
    one, two, three = ready_job(), None, None
    approve = reverse(
        "admin:distribution_channel_video_decide", args=[one.pk, "approve"]
    )
    assert admin_client.get(approve).status_code == 405
    response = admin_client.post(approve)
    assert response.status_code == 302 and response.url == videos_url()
    one.refresh_from_db()
    assert one.status == "approved" and one.decided_at
    two = queued(new_draft(hook="B"))
    render_stubbed(two)
    three = queued(
        videos.create(
            "firmware",
            "official:1",
            source=firmware_source(),
            pack_slug="firmware-release",
            preset="1x1",
        )[0]
    )
    render_stubbed(three)
    reject = reverse("admin:distribution_channel_video_decide", args=[two.pk, "reject"])
    admin_client.post(reject, {"note": "Caption too long"})
    two.refresh_from_db()
    assert two.status == "rejected" and two.decision_note == "Caption too long"
    admin_client.post(
        reverse("admin:distribution_channel_video_bulk"),
        {"action": "approve", "selected": [three.pk]},
    )
    three.refresh_from_db()
    assert three.status == "approved"
    assert (
        admin_client.post(
            reverse("admin:distribution_channel_video_decide", args=[one.pk, "explode"])
        ).status_code
        == 404
    )


def test_create_and_render_posts_validate_input(admin_client, make_post):
    post = make_post("A post that gets a manual video here")
    url = reverse("admin:distribution_channel_video_create")
    ok = admin_client.post(
        url,
        {
            "kind": "blog",
            "ref": f"post:{post.pk}",
            "template": "blog-post",
            "hook": "B",
        },
    )
    assert ok.status_code == 302
    job = VideoJob.objects.get(content_kind="blog", hook="B")
    assert job.params["auto"] is False
    admin_client.post(url, {"kind": "blog", "ref": "post:999999"})
    assert VideoJob.objects.count() == 1
    again = admin_client.post(
        reverse("admin:distribution_channel_video_render", args=[job.pk]),
        {"template": "nope", "hook": "A"},
    )
    assert again.status_code == 302 and VideoJob.objects.count() == 1


def test_scheduling_needs_an_approved_video(admin_client, make_post, make_channel):
    post = make_post("A post whose video will be scheduled soon")
    channel = make_channel("tiktok", name="tt-main")
    job, _ = videos.create("blog", f"post:{post.pk}", pack_slug="blog-post")
    assert job is not None
    schedule = reverse("admin:distribution_channel_video_schedule", args=[job.pk])
    admin_client.post(schedule, {"channels": [channel.pk]})
    assert not ShareJob.objects.filter(
        channel=channel
    ).exists()  # not approved: nothing queued
    VideoJob.objects.filter(pk=job.pk).update(status="approved", video_name="x/y.mp4")
    admin_client.post(schedule, {"channels": [channel.pk]})
    queued_job = ShareJob.objects.get(channel=channel)
    assert queued_job.object_ref == f"post:{post.pk}"
    admin_client.post(schedule, {"channels": [channel.pk]})
    assert ShareJob.objects.filter(channel=channel).count() == 1  # idempotent


def test_permissions_gate_every_video_action(staff_client):
    job = ready_job()
    viewer = staff_client("view_videojob")
    nobody = staff_client()
    assert viewer.get(videos_url()).status_code == 200
    assert nobody.get(videos_url()).status_code == 403
    for name, args in (
        ("video_decide", [job.pk, "approve"]),
        ("video_render", [job.pk]),
        ("video_schedule", [job.pk]),
        ("video_bulk", []),
        ("video_create", []),
    ):
        assert (
            viewer.post(
                reverse(f"admin:distribution_channel_{name}", args=args)
            ).status_code
            == 403
        ), name
    assert (
        nobody.get(
            reverse("admin:distribution_channel_video_media", args=[job.pk, "video"])
        ).status_code
        == 403
    )
    job.refresh_from_db()
    assert job.status == "ready"


def test_media_is_streamed_to_staff_with_ranges_and_never_cached(admin_client):
    job = ready_job()
    url = reverse("admin:distribution_channel_video_media", args=[job.pk, "video"])
    full = admin_client.get(url)
    assert (
        full.status_code == 200
        and full["Content-Type"] == "video/mp4"
        and full["Accept-Ranges"] == "bytes"
    )
    assert (
        "no-store" in full["Cache-Control"]
        and full["X-Content-Type-Options"] == "nosniff"
    )
    part = admin_client.get(url, HTTP_RANGE="bytes=0-9")
    assert (
        part.status_code == 206
        and len(part.content) == 10
        and part["Content-Range"].startswith("bytes 0-9/")
    )
    tail = admin_client.get(url, HTTP_RANGE="bytes=-8")
    assert tail.status_code == 206 and len(tail.content) == 8
    assert admin_client.get(url, HTTP_RANGE="bytes=999999-").status_code == 416
    assert (
        admin_client.get(
            reverse("admin:distribution_channel_video_media", args=[job.pk, "other"])
        ).status_code
        == 404
    )
    assert (
        admin_client.get(
            reverse("admin:distribution_channel_video_media", args=[999999, "video"])
        ).status_code
        == 404
    )
    from django.test import Client

    assert Client().get(url).status_code in {
        302,
        403,
    }  # anonymous visitors never reach it


def test_the_tab_and_picker_link_to_videos(admin_client):
    body = admin_client.get(
        reverse("admin:distribution_channel_dashboard")
    ).content.decode()
    assert videos_url() in body
