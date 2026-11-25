# Motion video engine (distribution)

Decision record and operating notes for videos enlisted for every published post
and verified firmware. Staff review and approve them before posting to TikTok,
YouTube Shorts or Instagram Reels. **Free, local rendering only**: no paid API,
SaaS, stock service or service requiring a key.

## Decision

**Python orchestrator + HTML/CSS/SVG motion templates**, drawn frame by frame in
headless Chromium (Playwright, deterministic virtual clock), then encoded by
ffmpeg. Audio is generated procedurally; voice-over is optional and offline.

```text
content (services) -> Source -> template pack
  -> Draft (beats + scenes) -> plan (timed, validated JSON)
plan -> runtime.js/css (window.__seek(ms))
  -> N frame workers (Chromium) -> JPEG frames
plan -> audio.write_bed (music + whoosh/pop) [+ offline TTS]
  -> ffmpeg loudnorm 2-pass (-14 LUFS)
frames + audio -> ffmpeg H.264 High, yuv420p, BT.709, +faststart
  -> 1080x1920 at 30 fps, AAC 160k
```

### Why this and not the alternatives (checked, not assumed)

* **Remotion:** Its free licence is limited to individuals and small companies;
  larger companies need a paid licence (`LICENSE.md`). It also needs Node and
  React. Rejected for licence risk and a second runtime.
* **Revideo (MIT):** A capable TypeScript motion framework, but Node-only and
  another stack to maintain beside Django. Chromium and Python already provide
  the same model: a page that is a pure function of time.
* **MoneyPrinterTurbo / ShortGPT:** These pipelines use LLM and stock-footage
  APIs (Pexels), cloud TTS (EdgeTTS) and MoviePy slideshows. Rejected because
  they need keys or cloud services, and the owner ruled out slideshows.
* **Postiz (AGPL-3.0), Mixpost (MIT), social-auto-upload (MIT):** Schedulers
  and uploaders, not renderers. Embedding Postiz would impose AGPL terms, and
  social-auto-upload drives browsers to log into accounts. Posting stays in
  the existing, audited platform adapters.
* **MoviePy 2:** Python clip compositing has slow per-frame effects and no
  layout or typography engine. Rejected for quality.
* **Manim:** Heavy mathematical animation (Cairo and LaTeX). Wrong tool.
* **Lottie / rlottie, skia-python, pycairo:** Vector or canvas drawing without
  a layout engine. We would rebuild text wrapping, flexbox, kerning and effects;
  rlottie also needs authored After Effects assets. Rejected as more code for
  less typography.
* **Pillow only (the old slideshow):** No motion, layout or kerning. Replaced;
  Pillow still draws share cards.
* **ffmpeg filters only** (`xfade`, `zoompan`, `drawtext`, `subtitles`): Good
  for transitions and burned captions, poor for layered kinetic typography,
  SVG icons, counters and charts. Used for encoding, loudness and ducking;
  motion is drawn in the page.

Licences of shipped tools: ffmpeg (`Gyan.FFmpeg` full build is GPL; called as
an external program, not linked), Chromium (BSD-style) through Playwright
(Apache-2.0), and segno (BSD) for QR codes.

### What is not possible for free (said plainly)

* No stock footage, AI image or video generation, or studio-quality neural
  voices: those need paid APIs or large GPU models. Art is generated (device
  silhouette cards, gradients, icons and QR codes); a device photo is used only
  when already stored on this site.
* No licensed music library. The music bed is **synthesised by this
  application** (a four-chord pad, bass, kick and hats; 118 BPM), so it carries
  no licence obligation (`audio.LICENCE`). It is simple by design.
* Voice-over needs an installed offline engine (`espeak-ng` is robotic; Piper
  with a voice model is much better). Without one, videos have captions and
  music only. Cloud TTS is never called.
* Other companies' brand logos are never drawn; only generic Lucide-style
  glyphs are used.

## Speed (measured on the owner's machine: Core i7-1165G7, 4 cores)

* `__seek(ms)` draws one frame in 13 ms.
* A software-GL screenshot of a 1080x1920 frame (JPEG q94) takes 190 ms;
  CDP capture was not faster.
* A 5 s self-test clip takes 37 s with one worker.
* A 22 s firmware clip takes **77 s** (673 frames) with four workers.

Frame capture is the cost, so up to `min(4, cpus/2)` Chromium processes draw
frames (`DISTRIBUTION_VIDEO_WORKERS` overrides). A 30 s clip needs about 100 s
on this machine, well inside the default limit.

**Hard limits.** `DistributionSettings.video_render_timeout` (default 300 s,
30 to 1800) bounds a render. After the limit, every frame worker and ffmpeg is
killed; the failure is *retryable* up to `MAX_ATTEMPTS = 3`, then `failed` with
the reason. Celery adds `soft_time_limit=1800` and `time_limit=1860` as an
outer net. `sweep_stale` fails a render whose process vanished.

## Output spec

* Presets: 1080x1920 (9:16, primary), 1080x1080 (1:1) and 1920x1080
  (16:9), all at 30 fps and **15 to 45 s**. The plan builder pads short videos,
  drops optional narration beats, compresses timing and rejects out-of-range
  videos.
* Encoding: H.264 High, `yuv420p`, BT.709, `-movflags +faststart`, keyframes
  every 2 s and AAC 160 kbps.
* Loudness: two-pass `loudnorm` to **-14 LUFS** integrated and -1.5 dBTP true
  peak (verified within 1.5 LU by the golden test).
* Safe zones leave space for TikTok, Shorts and Reels UI. Fractions are top,
  right, bottom, left; 9:16 uses 10%, 13%, 23%, 7%. A test measures every
  text box in every scene, pack and preset; overflowing scenes are scaled down.
* Captions are burned in word by word, timed to narration beats (about 2.7
  words per second, or measured from TTS audio when a voice exists).

## Template packs

Packs are data-driven and versioned in `video/packs.py`; the version is part
of a video's idempotency key. Every pack has an animated intro and outro,
kinetic typography, a progress bar, lower third, captions, brand watermark and
QR call to action.

* `firmware-release` v1: verified firmware; hook, parallax device hero,
  floating chips, generated device card or stored photo, animated facts,
  VERIFIED stamp, warning card and QR CTA.
* `flashing-guide` v1: posts that are guides with numbered steps; hook,
  numbered rows with animated tool icons on the narration beat, warning and
  CTA.
* `blog-post` v1: any post; kinetic headline, key points from headings or
  summary sentences, and CTA.
* `spotlight` v1: a brand or chipset; hook, latest verified firmware and CTA.
* `weekly-roundup` v1: the week's firmware; hook, animated bar chart by brand,
  list and CTA.
* `smoke` v1: a 3 s capability check; never enlisted.

Hook variant **A** states the news; **B** asks the viewer's question. Staff can
render both and compare. Presets: 9:16, 1:1 and 16:9.

**Safety of the render page.** The page is built in memory (`page_html`) with
meta CSP `default-src 'none'; connect-src 'none'; img-src data:`. It loads in
an `offline=True` browser context with a route that aborts every request. The
plan is embedded as `application/json` with `<`, `>`, `&`, U+2028 and U+2029
escaped. The runtime applies every string with `textContent`, so content
cannot inject markup or script. Tests render hostile titles such as
`</script><img onerror=...>`. Images come only from site storage through
`art.local_image`, which re-encodes them with Pillow; remote images are never
fetched.

## Pipeline, approval and posting

States: `draft -> rendering -> ready -> approved -> posted`, plus `rejected`
and `failed`.

* **Auto-enlist.** `handlers` calls `videos.enlist` in `transaction.on_commit`
  for each newly public post and approved firmware. The idempotency key includes
  content, template, version, preset and hook. `video_daily_cap` limits
  automatic drafts to 10 a day by default (0 = off), preventing bulk imports
  and seeds from flooding the queue. `video_auto_enlist` disables enlistment.
  Drafts are *not* rendered unless `video_auto_render` is on (default off) or
  staff press **Render**. A weekly roundup is enlisted once per ISO week when
  at least three firmware items landed.
* **Render.** Celery runs
  `apps.distribution.tasks.render_video` when `QUEUE_BACKEND=celery`;
  otherwise, a detached `manage.py distribution_jobs --render <id>` process
  runs it. Web requests never render. A `select_for_update` claim allows only
  one worker to render a job.
* **Approval gate (hard rule, tested).** Video platforms receive media only
  through `videos.approved_refs`, which returns files from `approved` or
  `posted` jobs. The old automatic slideshow is no longer used for posting.
  A `ready` or `rejected` video, or no video, leaves the share job in
  `needs_media` ("Waiting for an approved motion video"). Approval releases
  waiting posts. After a successful upload, `videos.note_posted` marks the job
  `posted`.
* **Posting** is unchanged: existing TikTok, YouTube and Instagram adapters,
  idempotent `ShareJob`s, circuit breaker and audit. Scheduling queues content
  on the chosen channels (idempotent per channel); WhatsApp Channel and other
  assisted platforms still use Hand-off.
* **Retention.** Hourly housekeeping purges old rendered files according to
  `media_retention_days` (never an `approved` one). Rejected and failed rows
  are deleted with their files.

## Staff screens

**Distribution > Videos** provides a capability report (ffmpeg, Chromium,
offline voice and fonts, with install commands), the daily-cap line, a create
form and a queue. The queue includes poster frame, inline `<video>` preview,
state badge, template/hook/length facts, approve, reject (reason), render again
with another template or hook, bulk approve/reject/render, and channel
scheduling. While a job renders, HTMX refreshes the queue every 5 s.

**Media library** lists only currently public firmware, published posts and
guides, active flashing tools, and brand/chipset spotlights with verified
firmware. It shows separate horizontal-card, vertical-card, legacy slideshow
and motion-video job states; missing storage files are not reported as ready.
Staff can generate both share-card sizes or create selected historical video
drafts. Each batch is limited to 10 items; video backfill uses the automatic
daily cap and publication idempotency key. It creates drafts only and never
starts rendering or posting. Flashing tools support cards only; the library
reports that they have no video template. Media is streamed to staff only
(`videos/<id>/video|poster/file/`, `private, no-store`, byte ranges for
seeking), not exposed publicly, and needs no CSP change (`media-src` falls
back to `default-src 'self'`).

## Capability report and installation

* **ffmpeg (required):** On Windows, run
  `winget install Gyan.FFmpeg --scope user` (no administrator rights needed;
  discovery checks the user WinGet folder). On Linux, run `apt install ffmpeg`;
  on macOS, `brew install ffmpeg`. Alternatively set `DISTRIBUTION_FFMPEG`.
* **Chromium (required):** Install with
  `pip install -e .[video]` and `playwright install chromium`.
* **Voice-over (optional):** Install `espeak-ng` with
  `winget install eSpeak-NG.eSpeak-NG`, or configure Piper using
  `DISTRIBUTION_PIPER` and `DISTRIBUTION_PIPER_MODEL`.
* **Fonts (optional):** Use a sans-serif such as Inter or DejaVu Sans for
  consistent text.

The Docker image does not contain Chromium or ffmpeg; rendering workers need
them (see the deployment notes), but the web process does not.

## Tests and the environment requirement

`tests/backend/distribution/test_video.py` covers plan duration and timing in
all presets, hook variants, parameter validation and escaping (including a
real Chromium render of hostile text and a check that no network request is
made). It also covers safe zones for every pack and preset, deterministic
frames, procedural audio, encoder recipe, state machine, claim exclusivity,
approval gate, daily cap and bulk import, idempotency, deadlines and worker
kill, retries and terminal failure, retention, stale sweep, staff permissions,
and ranged media streaming. `tests/frontend/test_distribution_video_browser.py`
checks the page at 360 and 1280 px in light and dark.

**There is no skip.** ffmpeg and Chromium are explicit requirements.
`test_environment_requirements` and the golden clip fail with install guidance
when a tool is missing. The golden clip renders for 3 s and uses ffprobe to
check H.264, 1080x1920, yuv420p, 30 fps, AAC, duration, frame count, loudness
near -14 LUFS, and faststart. CI must install both tools.
