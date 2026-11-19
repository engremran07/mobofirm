# Motion video engine (distribution)

Decision record and operating notes for the videos that are enlisted for every published post
and verified firmware, reviewed and approved by staff, and then posted to TikTok, YouTube
Shorts and Instagram Reels. **Free, local rendering only**: no paid API, SaaS or stock service
and nothing that needs a key.

## Decision

**Python orchestrator + HTML/CSS/SVG motion templates drawn frame by frame in headless
Chromium (Playwright, deterministic virtual clock) + ffmpeg for the encode.** Audio is generated
procedurally; voice-over is optional and offline.

```text
content (services) -> Source -> template pack -> Draft (beats + scenes) -> plan (timed, validated, JSON)
plan -> runtime.js/css (window.__seek(ms)) -> N frame workers (Chromium) -> JPEG frames
plan -> audio.write_bed (music + whoosh/pop) [+ offline TTS] -> ffmpeg loudnorm 2-pass (-14 LUFS)
frames + audio -> ffmpeg H.264 High, yuv420p, BT.709, +faststart, 1080x1920 @ 30 fps, AAC 160k
```

### Why this and not the alternatives (checked, not assumed)

| Option | Finding | Verdict |
| --- | --- | --- |
| **Remotion** | Free licence is limited to individuals and small companies, otherwise a paid company licence (its `LICENSE.md`); Node and a React toolchain. | Rejected: licence risk, second runtime. |
| **Revideo** (MIT) | Capable TypeScript motion framework, but Node-only, a second stack to maintain and deploy next to Django. | Not needed: Chromium + Python gives the same model (a page that is a pure function of time) without it. |
| **MoneyPrinterTurbo / ShortGPT** | Pipelines built on LLM APIs, stock footage APIs (Pexels), cloud TTS (EdgeTTS) and MoviePy slideshows. | Rejected: needs keys or cloud services, and the result is a slideshow, which the owner ruled out. |
| **Postiz (AGPL-3.0), Mixpost (MIT), social-auto-upload (MIT)** | Schedulers/uploaders, not renderers. Postiz is AGPL, so embedding it would impose AGPL terms; social-auto-upload drives browsers to log into accounts. | Not used. Posting stays in the existing, audited platform adapters. |
| **MoviePy 2** | Python compositing of clips; per-frame effects are slow and there is no layout or typography engine. | Rejected for quality. |
| **Manim** | Mathematical animation, heavy (Cairo, LaTeX). | Wrong tool. |
| **Lottie / rlottie, skia-python, pycairo** | Vector or canvas drawing without a layout engine; we would rebuild text wrapping, flexbox, kerning and effects by hand, and rlottie needs authored After Effects assets. | Rejected: more code for less typography. |
| **Pillow only (the old slideshow)** | No motion, no layout, no kerning. | Replaced. Pillow still draws the share cards. |
| **ffmpeg filters only** (`xfade`, `zoompan`, `drawtext`, `subtitles`) | Good for transitions and burned captions, poor for layered kinetic typography, SVG icons, counters and charts. | Used for what it is best at: encode, loudness, ducking. The motion is drawn in the page. |

Licences of what we do ship: ffmpeg (the `Gyan.FFmpeg` full build is GPL; we call it as an
external program, we do not link it), Chromium (BSD-style) through Playwright (Apache-2.0),
segno (BSD) for QR codes.

### What is not possible for free (said plainly)

* No stock footage, AI image or video generation, or studio-quality neural voices: those need
  paid APIs or large GPU models. Art is generated (device silhouette cards, gradients, icons,
  QR codes); a device photo is used only when one is already stored on this site.
* No licensed music library. The music bed is **synthesised by this application** (a four-chord
  pad, bass, kick, hats; 118 BPM) so it carries no licence obligation (`audio.LICENCE`).
  It is simple by design.
* Voice-over needs an offline engine you install (`espeak-ng` is robotic; Piper with a voice
  model is much better). Without one, videos have captions and music only. Cloud TTS is never
  called.
* Brand logos of other companies are never drawn (Lucide-style generic glyphs only).

## Speed (measured on the owner's machine: Core i7-1165G7, 4 cores)

| Step | Time |
| --- | --- |
| `__seek(ms)` (draw one frame) | 13 ms |
| Screenshot of a 1080x1920 frame (software GL, JPEG q94) | 190 ms (CDP capture was not faster) |
| 5 s self-test clip, one worker | 37 s |
| 22 s firmware clip, 4 workers | **77 s** (673 frames) |

Frame capture is the cost, so frames are drawn by up to `min(4, cpus/2)` Chromium processes
(`DISTRIBUTION_VIDEO_WORKERS` overrides). A 30 s clip needs about 100 s on this machine, well
inside the default limit.

**Hard limits.** `DistributionSettings.video_render_timeout` (default 300 s, 30 to 1800) bounds a
render: past it every frame worker and ffmpeg is killed and the failure is *retryable* (at most
`MAX_ATTEMPTS = 3`, then `failed`, with the reason). Celery adds `soft_time_limit=1800`,
`time_limit=1860` as an outer net. A render whose process vanished is failed by `sweep_stale`.

## Output spec

* 1080x1920 (9:16, primary), 1080x1080 (1:1) and 1920x1080 (16:9) presets; 30 fps; **15 to 45 s**
  (the plan builder pads short videos, drops optional narration beats, then compresses timing,
  and refuses anything outside the range).
* H.264 High, `yuv420p`, BT.709 tagged, `-movflags +faststart`, keyframe every 2 s; AAC 160 kbps.
* Loudness: two-pass `loudnorm` to **-14 LUFS** integrated, true peak -1.5 dBTP (verified in the
  golden test within 1.5 LU).
* Safe zones per preset (fractions of the frame kept free of text for TikTok, Shorts and Reels
  UI: top, right, bottom, left; 9:16 is 10 %, 13 %, 23 %, 7 %). A test measures every text box
  of every scene of every pack in every preset against them; scenes that would overflow are scaled
  down to fit.
* Captions are burned into the picture word by word, timed to the narration beats (estimated
  at about 2.7 words per second, or measured from the TTS audio when a voice exists).

## Template packs

Data-driven and versioned (`video/packs.py`); the version is part of a video's idempotency key.
Every pack has an animated intro and outro, kinetic typography, a progress bar, a lower third,
captions, a brand watermark, and a QR call to action to the page.

| Pack | Content | Scenes |
| --- | --- | --- |
| `firmware-release` v1 | verified firmware | hook, device hero (parallax, floating chips, generated device card or a stored photo), facts (animated counters, VERIFIED stamp), warning card, QR CTA |
| `flashing-guide` v1 | posts that are guides with numbered steps | hook, steps (numbered rows with animated tool icons, appearing on the narrated beat), warning, CTA |
| `blog-post` v1 | any post | kinetic headline, key points (headings or summary sentences), CTA |
| `spotlight` v1 | a brand or chipset | hook, list of the latest verified firmware, CTA |
| `weekly-roundup` v1 | the week's firmware | hook, animated bar chart by brand, list, CTA |
| `smoke` v1 | none (self-test) | 3 s capability check; never enlisted |

Hook variant **A** states the news, **B** asks the viewer's question; staff can render both and
compare. Presets: 9:16, 1:1, 16:9.

**Safety of the render page.** The page is built in memory (`page_html`) with a meta CSP of
`default-src 'none'; connect-src 'none'; img-src data:`, loaded with the browser context
`offline=True` and a route that aborts every request. The plan is embedded as `application/json`
with `<`, `>`, `&`, U+2028 and U+2029 escaped, and the runtime applies every string with
`textContent`, so content cannot inject markup or script (tests render `</script><img
onerror=...>` titles). Images come only from this site's media storage through `art.local_image`,
which re-encodes them with Pillow; remote images are never fetched.

## Pipeline, approval and posting

States: `draft -> rendering -> ready -> approved -> posted`, with `rejected` and `failed`.

* **Auto-enlist.** `handlers` calls `videos.enlist` in `transaction.on_commit` for every newly
  public post and every approved firmware. Idempotent on (content, template, version, preset,
  hook); capped by `video_daily_cap` automatic drafts a day (default 10; 0 = off), so a bulk import
  or a seed cannot flood the queue; switched off by `video_auto_enlist`. A draft is *not* rendered
  unless `video_auto_render` is on (default off) or staff press **Render**. A weekly roundup draft is
  enlisted once per ISO week when at least three firmware landed.
* **Render** runs in Celery (`apps.distribution.tasks.render_video`) when `QUEUE_BACKEND=celery`,
  otherwise as a detached `manage.py distribution_jobs --render <id>` process, so a web request
  never renders. A claim (`select_for_update`) lets only one worker render a job.
* **Approval gate (hard rule, tested).** Video platforms get media only through
  `videos.approved_refs`, which returns the file of an `approved` or `posted` job and nothing else.
  The old automatic slideshow is no longer used for posting. A `ready` video, a `rejected` one or no
  video leaves the share job in `needs_media` ("Waiting for an approved motion video"). Approving
  releases waiting posts. After a successful upload `videos.note_posted` marks the job `posted`.
* **Posting** is unchanged: the existing TikTok, YouTube and Instagram adapters, idempotent
  `ShareJob`s, circuit breaker and audit. Scheduling a video queues the content on the chosen
  channels (idempotent per channel); WhatsApp Channel and other assisted platforms still use Hand-off.
* **Retention.** `media_retention_days` purges old rendered files (never an `approved` one) from the
  hourly housekeeping; rejected and failed rows are deleted with their files.

## Staff screens

**Distribution > Videos**: capability report (ffmpeg, Chromium, offline voice, fonts, with install
commands), the daily-cap line, a create form, and a queue with poster frame, an inline `<video>`
preview, state badge, template/hook/length facts, approve, reject (reason), render again with
another template or hook, bulk approve/reject/render, and schedule to channels. While anything is
rendering the queue refreshes itself every 5 s over HTMX. Media is streamed to staff only
(`videos/<id>/video|poster/file/`, `private, no-store`, byte ranges for seeking), so it is not a
public address and needs no CSP change (`media-src` falls back to `default-src 'self'`).

## Capability report and installation

| Tool | Required | Install |
| --- | --- | --- |
| ffmpeg | yes | Windows: `winget install Gyan.FFmpeg --scope user` (works without administrator rights; found automatically in the user WinGet folder). Linux: `apt install ffmpeg`. macOS: `brew install ffmpeg`. Or set `DISTRIBUTION_FFMPEG`. |
| Chromium | yes | `pip install -e .[video]` then `playwright install chromium` |
| Voice-over | no | `espeak-ng` (`winget install eSpeak-NG.eSpeak-NG`) or Piper (`DISTRIBUTION_PIPER`, `DISTRIBUTION_PIPER_MODEL`) |
| Fonts | no | A sans-serif such as Inter or DejaVu Sans for consistent text |

The Docker image does not contain Chromium or ffmpeg; rendering workers need them (see the
deployment notes). The web process does not.

## Tests and the environment requirement

`tests/backend/distribution/test_video.py`: plan duration and timing in all presets, hook variants,
parameter validation, escaping (including a real Chromium render of hostile text and a check that no
network request is made), safe zones for every pack and preset, deterministic frames, procedural
audio, the encoder recipe, state machine, claim exclusivity, the approval gate, daily cap and bulk
import, idempotency, deadline and worker kill, retry then terminal failure, retention, stale sweep,
the staff page and permissions, and ranged media streaming. `tests/frontend/test_distribution_video_browser.py`
checks the page at 360 and 1280 px in light and dark.

**There is no skip.** ffmpeg and Chromium are an explicit requirement of this suite:
`test_environment_requirements` and the golden clip (a real 3 s render, checked with ffprobe:
H.264, 1080x1920, yuv420p, 30 fps, AAC, duration, frame count, loudness near -14 LUFS, faststart)
fail with install guidance when a tool is missing. CI must install both.
