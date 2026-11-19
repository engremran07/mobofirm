"""Renderer: headless Chromium draws every frame, ffmpeg encodes.

Chromium (Playwright) loads one self-contained page and, for each frame, ``window.__seek(ms)``
draws exactly that moment, so the output depends only on the plan, never on wall-clock
timing. Frames are JPEG files drawn by one or more worker processes (software rendering
costs about 0.2 s per 1080x1920 frame, so a 30 s clip uses 3 to 4 workers). ffmpeg then
encodes H.264 (yuv420p, BT.709, ``+faststart``) and muxes the audio mastered to -14 LUFS.

Everything runs under a hard deadline; on timeout every worker and ffmpeg is killed and the
failure is reported as retryable. Nothing is fetched from a network.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Self

from apps.distribution.video import audio, capabilities
from apps.distribution.video.plan import page_html

logger = logging.getLogger(__name__)
JPEG_QUALITY = 94
OP_TIMEOUT_MS = 20_000
POSTER_AT = 0.36
LOG_LIMIT = 4000
MAX_WORKERS = 4
ENV_WORKERS = "DISTRIBUTION_VIDEO_WORKERS"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class RenderError(RuntimeError):
    """A render stopped; ``retryable`` says whether trying again can help."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class RenderResult:
    path: Path
    poster: bytes
    duration_ms: int
    size_bytes: int
    checksum: str
    voice: bool
    log: str = ""
    seconds: float = 0.0
    frames: int = 0


class Encoder(Protocol):
    def encode(
        self,
        plan: Mapping[str, Any],
        work: Path,
        frames: Path,
        soundtrack: Path | None,
        deadline: float,
    ) -> Path: ...


def worker_count() -> int:
    configured = os.environ.get(ENV_WORKERS, "")
    if configured.isdigit() and int(configured) > 0:
        return min(int(configured), 8)
    return max(1, min(MAX_WORKERS, (os.cpu_count() or 2) // 2))


class ChromiumFrames:
    """Draws frames in one headless Chromium with the network switched off."""

    def __init__(self, plan: Mapping[str, Any]) -> None:
        self.plan = plan
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    def __enter__(self) -> Self:
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                args=["--disable-gpu", "--hide-scrollbars"]
            )
            context = self._browser.new_context(
                viewport={"width": self.plan["w"], "height": self.plan["h"]},
                device_scale_factor=1,
                offline=True,
            )
            context.route("**/*", lambda route: route.abort())
            self._page = context.new_page()
            self._page.set_default_timeout(OP_TIMEOUT_MS)
            self._page.set_content(page_html(self.plan), wait_until="load")
            self._page.wait_for_function("window.__ready === true")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        from playwright.sync_api import Error as PlaywrightError

        for closer in (
            lambda: self._browser and self._browser.close(),
            lambda: self._playwright and self._playwright.stop(),
        ):
            try:
                closer()
            except (PlaywrightError, OSError, RuntimeError):
                logger.warning("Chromium did not close cleanly", exc_info=True)
        self._browser = self._playwright = self._page = None

    @property
    def page(self) -> Any:
        """The live Playwright page (for tests that inspect the rendered DOM)."""
        return self._page

    def frame(self, ms: float) -> bytes:
        self._page.evaluate("t => window.__seek(t)", ms)
        return self._page.screenshot(
            type="jpeg", quality=JPEG_QUALITY, animations="disabled"
        )


class FfmpegEncoder:
    """ffmpeg: a JPEG sequence plus the mastered audio into one H.264/AAC MP4."""

    def __init__(self, binary: str) -> None:
        self.binary = binary

    def command(
        self, plan: Mapping[str, Any], work: Path, frames: Path, soundtrack: Path | None
    ) -> list[str]:
        cmd = [
            self.binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            str(plan["fps"]),
            "-i",
            str(frames / "%05d.jpg"),
        ]
        if soundtrack is not None:
            cmd += [
                "-i",
                str(soundtrack),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar",
                "44100",
                "-ac",
                "2",
            ]
        else:
            cmd += ["-map", "0:v", "-an"]
        cmd += [
            "-vf",
            f"scale={plan['w']}:{plan['h']}:flags=lanczos:in_range=pc:out_range=tv:out_color_matrix=bt709,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-r",
            str(plan["fps"]),
            "-g",
            str(plan["fps"] * 2),
            "-t",
            f"{plan['total'] / 1000:.3f}",
            "-movflags",
            "+faststart",
            str(work / "out.mp4"),
        ]
        return cmd

    def encode(
        self,
        plan: Mapping[str, Any],
        work: Path,
        frames: Path,
        soundtrack: Path | None,
        deadline: float,
    ) -> Path:
        try:
            run = subprocess.run(
                self.command(plan, work, frames, soundtrack),
                capture_output=True,
                timeout=max(5.0, deadline - time.monotonic()),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RenderError("Encoding timed out.", retryable=True) from error
        except OSError as error:
            raise RenderError(f"ffmpeg could not run: {error}") from error
        output = work / "out.mp4"
        if run.returncode != 0 or not output.exists():
            raise RenderError(
                "ffmpeg failed: " + run.stderr.decode(errors="replace")[-300:]
            )
        return output


def _check_deadline(deadline: float, limit: float) -> None:
    if time.monotonic() > deadline:
        raise RenderError(
            f"The render exceeded its {int(limit)} s limit.", retryable=True
        )


def _frames_sequential(
    plan: Mapping[str, Any],
    frames: Path,
    factory: Callable[[Mapping[str, Any]], Any],
    deadline: float,
    limit: float,
) -> int:
    total = round(plan["total"] / 1000 * plan["fps"])
    with factory(plan) as source:
        for index in range(total):
            _check_deadline(deadline, limit)
            (frames / f"{index:05d}.jpg").write_bytes(
                source.frame(index * 1000 / plan["fps"])
            )
    return total


def _frames_parallel(
    plan: Mapping[str, Any],
    work: Path,
    frames: Path,
    deadline: float,
    limit: float,
    workers: int,
) -> int:
    total = round(plan["total"] / 1000 * plan["fps"])
    plan_path = work / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    procs: list[subprocess.Popen[bytes]] = []
    try:
        for first in range(workers):
            procs.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "apps.distribution.video.frame_worker",
                        str(plan_path),
                        str(frames),
                        str(first),
                        str(workers),
                        str(total),
                    ],
                    cwd=PROJECT_ROOT,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            )
        while any(p.poll() is None for p in procs):
            _check_deadline(deadline, limit)
            time.sleep(0.2)
        for proc in procs:
            if proc.returncode != 0:
                detail = (proc.stderr.read() if proc.stderr else b"").decode(
                    errors="replace"
                )[-300:]
                raise RenderError(f"A frame worker failed: {detail}", retryable=True)
    except BaseException:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
        for proc in procs:
            proc.wait(timeout=15)
        raise
    return total


def render_video(
    plan: Mapping[str, Any],
    out_dir: Path,
    *,
    timeout_s: float,
    voices: Sequence[audio.Voice] = (),
    frames_factory: Callable[[Mapping[str, Any]], Any] | None = None,
    encoder: Encoder | None = None,
    workers: int | None = None,
    with_audio: bool = True,
) -> RenderResult:
    """Render ``plan`` to ``out_dir/video.mp4`` within ``timeout_s`` seconds (hard).

    ``frames_factory`` and ``encoder`` are seams for tests; production uses the parallel
    Chromium workers and ffmpeg.
    """
    started = time.monotonic()
    deadline = started + timeout_s
    binary = capabilities.ffmpeg_path()
    if encoder is None:
        if not binary:
            raise RenderError("ffmpeg was not found. " + capabilities.INSTALL_FFMPEG)
        encoder = FfmpegEncoder(binary)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="video-render-") as scratch:
        work = Path(scratch)
        frames = work / "frames"
        frames.mkdir()
        soundtrack: Path | None = None
        if with_audio:
            audio.write_bed(
                work / "bed.wav",
                int(plan["total"]),
                int(plan["seed"]),
                [int(s["start"]) for s in plan["scenes"]],
            )
            if binary:
                try:
                    soundtrack = audio.master(
                        binary, plan, work, voices, deadline - time.monotonic()
                    )
                except audio.MasterError as error:
                    raise RenderError(
                        f"Audio failed: {error}", retryable=True
                    ) from error
        count = workers if workers is not None else worker_count()
        if frames_factory is None and count > 1:
            total = _frames_parallel(plan, work, frames, deadline, timeout_s, count)
        else:
            total = _frames_sequential(
                plan, frames, frames_factory or ChromiumFrames, deadline, timeout_s
            )
        poster_path = frames / f"{int(total * POSTER_AT):05d}.jpg"
        poster = poster_path.read_bytes() if poster_path.exists() else b""
        produced = encoder.encode(plan, work, frames, soundtrack, deadline)
        final = out_dir / "video.mp4"
        final.write_bytes(produced.read_bytes())
    size = final.stat().st_size
    if size < 1024:
        raise RenderError("The encoder produced an empty file.")
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    seconds = time.monotonic() - started
    log = (
        f"{total} frames {plan['w']}x{plan['h']} @ {plan['fps']} fps in {seconds:.1f} s "
        f"({count} worker(s)); {size // 1024} KiB; voice={'yes' if voices else 'no'}"
    )
    return RenderResult(
        final,
        poster,
        int(plan["total"]),
        size,
        digest,
        bool(voices),
        log[:LOG_LIMIT],
        seconds,
        total,
    )
