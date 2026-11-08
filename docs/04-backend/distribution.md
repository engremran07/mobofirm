# Distribution: automatic sharing and growth engine

`apps/distribution` shares newly approved firmware and first-published human blog posts
to the owner's own social and messaging channels, in a platform-native way, and measures
what comes back. Everything is plain HTTPS through `apps.seo.fetcher.SafeFetcher`; there
are no paid SDKs and no new dependencies. Channels are **off** until staff enable them.

## Where it lives in the admin

A top-level sidebar group **Distribution**:

| Entry | What it is |
| --- | --- |
| Dashboard | Per-channel health (state, last success, circuit breaker, 7-day sent/failed/queued/clicks), queue depth, approvals waiting, top shared content, **Run queue now**, per-channel **Test connection**, **Send test post** and **Preview**. |
| Channels | One page per destination: connection (platform-specific fields and a "how to get credentials" hint), what to share, schedule and approval, status. Add via the platform picker. |
| Campaign presets | Copy and schedule presets (opening styles, emoji, hashtag cap, UTM, referral, A/B, evergreen, media). |
| Content calendar | Upcoming and just-sent posts grouped by day; reschedule or cancel what is waiting. |
| Approval queue | Posts from channels that require approval: edit the text, then approve or reject. |
| Share queue / Short links / Distribution log / Generated media / Retention settings | Model admins (queue is read-only except retry, cancel, approve). |
| Link analytics | Clicks per channel, content and opening style, "clicks per post", best-hour heatmap, CSV export. |

All staff pages render in the Unfold shell (`render_admin_page`, `mf-ui` kit,
`frontend/src/styles/admin-ui-dist.css`). Every change is a CSRF-protected POST with a
redirect and a message; no native dialogs and no inline scripts.

## Platforms (adapter registry)

`apps/distribution/channels.py` defines `Adapter`; `platforms.py` holds one class per
platform registered with `@register`. Adding a platform = one adapter class plus one line
in `Channel.Kind` (`tests/backend/distribution/test_adapters.py` fails if the two differ).
An adapter declares its credential fields, media requirement, message limit, hashtag
limit, link counting, minimum gap and daily ceiling, and implements `publish`, `check`
(read-only connection test) and `preview` (dry run).

| Platform | Media | How it posts |
| --- | --- | --- |
| Generic webhook | text | Signed JSON (HMAC-SHA256) with an idempotency key. |
| Telegram | text (+ optional image) | `sendMessage` / `sendPhoto` with a bot token. |
| Discord | text (+ optional image) | Channel webhook; mentions are disabled. |
| Slack | text | Incoming webhook. |
| X | text | `POST /2/tweets`; optional refresh token (rotation persisted). Links count as 23. |
| Mastodon | text | `/api/v1/statuses` on the configured server, `Idempotency-Key`. |
| Bluesky | text | `createSession` + `createRecord` with link/tag facets (UTF-8 byte offsets) and a link card. |
| Facebook Page | text / image | Graph API `/feed` or `/photos`. |
| Instagram | image (or Reel) | Graph container + `media_publish`; Reels poll `status_code`. |
| Threads | text | Container + `threads_publish`. |
| LinkedIn page | text | `/rest/posts` article share. |
| Reddit | text | Refresh token exchange + `/api/submit` link post; errors in a 200 body are failures. |
| Pinterest | image | `/v5/pins` from the share card. |
| TikTok | **video** | Content Posting API, `PULL_FROM_URL` (privacy defaults to private until the app is audited). |
| YouTube | **video** | Shorts upload (multipart) with OAuth refresh token. Community posts have no API. |
| WhatsApp | text | Cloud API template message to opted-in recipients (WhatsApp Channels have no posting API). |

Multi-step platforms keep their progress in `ShareJob.remote_state` (container ids,
recipients already sent), so a retry never creates a second container or message.

## Credentials

Platform fields are stored by kind: non-secret values in `Channel.options` (or the legacy
`target` / `endpoint_url` columns) and secrets in `secret_encrypted`, encrypted with the
project's single credential key (`apps.site_settings.crypto`). One-secret platforms keep
the legacy plain-string layout (existing rows keep working); platforms with several
secrets store an encrypted JSON object. A blank secret on save keeps the stored one;
secrets are never rendered, logged or placed in job rows, and are scrubbed from every
stored error message. The platform's own error reason (for example a Graph API message)
is kept in `last_error` so staff can act on it.

## Pipeline and failure semantics

`services.enqueue` creates at most one `ShareJob` per `(channel, content, round)`
(`idempotency_key = <channel>:<kind>:<object>[:r<round>]`) and one `ShortLink`, in one
transaction. Delivery (`process_due_jobs`, every minute from Celery beat) leases a job,
sends it and records one `ShareLog` line per decision:

* 2xx -> `sent`; 408/425/429/5xx and network errors -> retry with backoff
  (`RETRY_BACKOFF`, `Retry-After` honoured) until `max_attempts`, then `failed`;
  any other 4xx, a blocked destination or bad configuration -> `failed` immediately.
* **Circuit breaker:** `CHANNEL_TRIP_AFTER` (10) consecutive failures switch the channel
  off with a visible reason. Test posts never count.
* **Needs media:** a platform that needs an image or video the server cannot produce
  (no Pillow, no ffmpeg) parks the job as `needs_media` without spending an attempt; it
  is rechecked every 30 minutes and dropped (`skipped`, with the reason) after 7 days.
  It is never reported as sent.
* **Rate limits:** each adapter has a minimum gap between posts and a daily ceiling
  (for example X 10 min / 50 per day, Reddit 15 min / 5 per day); scheduling spaces posts
  by the gap and delivery defers a job that would break it.

## Approval modes, windows, quiet hours, caps

Per channel: `auto` (send as soon as the schedule allows), `approval` (jobs are `pending`
until staff approve; text can be edited first; the approved post is scheduled into the
next allowed slot) and `schedule_only` (never immediate: posts wait for the best-time
windows, using the platform default windows if none are set). `send_windows`
(`09:00-11:00,18:00-21:00`) and quiet hours are evaluated in the channel's time zone.
`daily_limit` (rolling 24 h, extras are recorded as `skipped`) and `kind_daily_cap`
(per content type per local day, extras move to the next day) bound volume. A *trending*
item (model or chipset created in the last 14 days) skips the queue backlog and gets two
extra hashtags, but still respects windows and quiet hours.

## Composer (viral content engine)

`composer.py` is pure: hook first, one detail line, the tracked link, then bounded
hashtags, fitted to the platform's counted length (hashtags go first, then the detail,
then the headline; the link is never trimmed). Opening styles: question, benefit,
how-to-flash, new/just-added, and evergreen. Rules: no "free" claims, "just added" only on
first shares (re-shares say "still useful"), gated content (paid/featured firmware, featured
or paid posts) is a teaser with no download wording and never the body. Hashtags come from
brand, model, chipset and tags. Emoji policy per preset: none, minimal, expressive.

* **Tracking:** each share gets `/go/<code>/` (first-party redirect owned by distribution)
  whose stored target carries UTM parameters and, when the preset allows and the channel
  has a referral account, `ref=<code>` through `AppService.get("referral").referral_code_for`.
* **A/B:** with `ab_test`, opening styles rotate (least used first) until
  `ab_min_clicks` clicks exist and every style was posted twice; then the style with the
  most clicks per post wins and is always used (see Link analytics).
* **Evergreen:** per preset, top-clicked items already shared on a channel are re-shared
  as `...:r<round>` jobs, at most `evergreen_per_week` per channel, never within
  `evergreen_gap_days`, only while still public.

## Short links, privacy and safety

`/go/<code>/` answers GET/HEAD only. A counted click stores just the hour it happened
(no IP, user agent, cookie or referrer), so it needs no consent. Bots, link-preview
fetchers, HEAD requests and prefetches increase `bot_clicks` and are never counted as
clicks. The destination is stored at creation and re-checked on every redirect to belong
to this site, so the endpoint cannot be an open redirect. Responses are `302`, `no-store`
and `X-Robots-Tag: noindex`.

## Media

`media.py` draws 1200x630 and 1080x1920 share cards with Pillow and, when `ffmpeg` is on
`PATH`, a 9-second vertical MP4 slideshow (three frames) for Shorts / Reels / TikTok. Files
are written through Django's default storage under `distribution/` (MEDIA); platforms that
fetch a URL (Instagram, Pinterest, TikTok) need the media location to be publicly
reachable over https. The dashboard shows what the server can generate.

## Retention

`DistributionSettings` (admin: "Retention settings"): click records (default 180 days;
per-link totals stay), audit log lines (365) and generated media (30); `0` keeps forever.
`automation.housekeeping` purges and runs the evergreen pass at most once an hour from the
minutely task (`distribution_jobs --housekeeping` forces it).

## Tests

`tests/backend/distribution/` (adapters per platform, composer, scheduling, short links,
approval, automation, media, admin pages and permissions) and
`tests/frontend/test_distribution_browser.py` (Playwright: pages at 360/768/1365 px,
light and dark, no console/CSP errors, run queue, test connection, approve, cancel).
Tests use a fake transport and in-memory storage and never call the network.
