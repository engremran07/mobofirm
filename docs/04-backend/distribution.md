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
| Hand-off | Posts for assisted platforms (WhatsApp Channel): copy-ready text, the channel link, **Mark as posted**. |
| Share queue / Short links / Distribution log / Generated media / Retention settings | Model admins (queue is read-only except retry, cancel, approve). |
| Link analytics | Clicks per channel, content and opening style, "clicks per post", best-hour heatmap, CSV export. |

All staff pages render in the staff console shell (`render_admin_page`, `admin/staff_page.html`
and the shared console components). Every change is a CSRF-protected POST with a
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
| WhatsApp (Cloud API) | text | Official Cloud API, **to opted-in people only**: an approved template (`{{1}}` title, `{{2}}` link), or free text while that person's 24 hour window is open. See "WhatsApp" below. |
| WhatsApp Channel (assisted) | none | **Assisted, not automatic.** WhatsApp Channels (newsletters) have no posting API. The job becomes *Ready to post by hand* with copy-ready text and the channel link; it is only counted as sent when staff press **Mark as posted**. |

Multi-step platforms keep their progress in `ShareJob.remote_state` (container ids,
recipients already sent), so a retry never creates a second container or message.

### The platform menu

`apps/distribution/catalog.py` is the presentation catalogue (pure data, no model): category,
Lucide icon (no brand logos exist in Lucide), sign-in method, setup difficulty, credential steps
and official documentation links per platform. A test keeps it in step with the registry.
**Channels > Add** (`/admin/distribution/channel/platforms/`) is a left/top category menu
(Social networks, Video, Messaging & communities, Fediverse & open, Developer & automation;
TikTok sits in two), a search box (`q`, filtered over HTMX into `#dist-picker-results`, works
as plain GET without JavaScript), a *Connected* section and per-category sections of cards.
**Setup guide** opens `/admin/distribution/channel/platforms/<kind>/requirements/` in
`#console-dialog` (a full page without HTMX). The add form is five steps: Platform, Credentials,
Content rules, Schedule & approval, Test & status; a plain save redirects to the channel page,
where **Test connection** and **Send test post** (the existing actions) finish the setup.

### WhatsApp

What is and is not possible (Meta's public documentation, linked in the setup guide; platform rules change, so re-check them):

* **Cloud API (supported).** Business-initiated messages need the person's prior opt-in. Outside the 24 hour customer-service window only an approved *template* may be sent; inside it free text is allowed. Marketing templates are throttled per recipient (error `131049`); `131050` means the person stopped marketing messages and is treated as an opt-out. Sending is limited per phone number and by the account's messaging tier.
* **Opt-in list.** `WhatsAppRecipient` rows (channel, number, opted in/out with source and reason, last inbound time, last delivery status). Numbers listed in the channel's *Opted-in recipients* field are recorded as "staff list" (staff attest consent). An opted-out number is never messaged, even while still listed. A number refused for good is recorded and the other recipients are still served; a retry never messages anyone twice (`remote_state.sent_to` / `failed_to`).
* **Webhook** `/go/whatsapp/<channel id>/webhook/` (optional): `GET` answers Meta's `hub.challenge` when the verify token matches; `POST` requires a valid `X-Hub-Signature-256` for the stored app secret (otherwise 403; 404 while no app secret is stored). It records delivery status (`sent`, `delivered`, `read`, `failed`), inbound time (opens the 24 hour window), STOP (opt out) and START (opt back in) for numbers that are already known; an unknown number never creates a recipient. It is the only CSRF-exempt view of the app: it carries no session authority and is authenticated by the HMAC signature.
* **WhatsApp Channels (not supported by any API).** Only the channel admins can post, from the WhatsApp app. The assisted platform prepares the post and never claims it was sent: **Distribution > Hand-off** lists the posts (`ShareJob.Status.HANDOFF`) with the text and channel link, and **Mark as posted** (`services.mark_posted`, idempotent) records the success.

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

## Feeds, Google News and push (syndication)

Feeds are first-class distribution output, owned here (`feeds.py`, `feed_views.py`,
`syndication.py`, `news.py`; content is read through `django.apps` model lookup, no
cross-app imports). Staff page: **Distribution > Feeds & Google News**.

**Public feeds** (`/feeds/`, index page with autodiscovery): `latest`, `section/blog`,
`section/firmware`, on-demand `brand/<slug>`, `chipset/<slug>` and `tag/<slug>`, and
`google-news`, each as `.rss` (RSS 2.0 with `content:encoded`, `dc:creator`,
`media:content`, `atom:link rel=self` (and `rel=hub`), `lastBuildDate`, `ttl`, permalink
`guid`, categories), `.atom` (Atom 1.0) and `.json` (JSON Feed 1.1 with `hubs`). Only live
posts (`Post.published()`) and `is_verified`, `is_active` firmware are listed; gated posts
carry a teaser and the page link, firmware only its page link (never a download URL);
drafts, scheduled and pending items never appear. `lastBuildDate` is the newest item time,
so output is deterministic: `ETag`, `Last-Modified`, `If-None-Match` / `If-Modified-Since`
(304), gzip when accepted, `Cache-Control: public, max-age=300`. Fetches are counted per
feed and hour (`FeedStat`, counts only; the in-process SEO crawler is not counted) and
`FeedConfig` keeps last fetched/built, size and item count. Per-feed on/off and the global
switch (`DistributionSettings.feeds_enabled`, `feeds_dynamic_enabled`, `feed_item_limit`)
make a feed answer 404. Empty or unknown dynamic feeds are 404, so slugs cannot be
probed into the database. The existing `blog:feed_*` feeds are unchanged.

**Google News** is not automatic. The page shows the facts and does only what is real:
(a) a status card for the opt-in news sitemap owned by `apps.seo` with copyable URLs and a
checklist (Publisher Center, Search Console, submit) that staff mark by hand
(`DistributionSettings.news_checklist`; it is a note, Google confirms nothing); (b) the
`google-news` feed, full text, only posts the news sitemap lists (last 48 hours), and
only while the news sitemap is on; (c) a Discover-readiness scan of the newest posts
(title length, cover image served locally and its width when measurable, date, author,
canonical, noindex) with links to the post and the SEO issues. Image dimensions of
remote images and the NewsArticle/`max-image-preview:large` markup are not measured here;
the page points at the SEO scan.

**Push.** WebSub: with a hub URL (https) in the settings, every newly public item queues a
`PingOutbox` row per affected feed and format (`websub:<hub>:<feed url>:<entry>` is the
unique idempotency key). IndexNow: one row per URL through `apps.seo.indexnow.submit`,
only while SEO has it enabled. Transaction boundary: the publish handlers call
`syndication.announce` in `transaction.on_commit` (never inside the content
transaction), which only writes rows; the minutely job (`process_share_jobs`,
`distribution_jobs`) delivers them with `RETRY_BACKOFF`, `MAX_ATTEMPTS = 5`, terminal
`failed` afterwards (staff can retry or cancel), and writes a `ShareLog` audit line for
every decision. Removing the hub makes pending pings fail with a clear reason. Default is
off: no hub, no pings, no `rel=hub`. Browser Web Push (VAPID) already exists in the Users
app for account notifications and needs `pywebpush`, which this site does not install, so
it is shown as *not enabled*; feeds do not send browser push. On Android, reach is: a
feed-reader app following a feed (automatic after subscribing, faster with WebSub) and the
Chrome home-screen shortcut (manual, no subscription). Retention: `FeedStat` and finished
pings follow `log_retention_days`.

Migrations: `0004_whatsapp_assisted_optin`, `0005_feeds_syndication`.

## Motion videos

Videos for TikTok, YouTube Shorts and Reels are rendered locally and approved by staff before
anything posts. Full decision record, template packs, limits and installation:
[`distribution-video.md`](distribution-video.md). Staff screens: **Distribution > Videos** and
**Distribution > Media library**.
Migration `0006_motion_video_jobs` (`VideoJob`, four `DistributionSettings` fields).

## Tests

`tests/backend/distribution/` (adapters per platform, composer, scheduling, short links,
approval, automation, media, admin pages and permissions, platform picker and WhatsApp,
feeds and the ping outbox) and
`tests/frontend/test_distribution_browser.py` (Playwright: pages at 360/768/1365 px,
light and dark, no console/CSP errors, run queue, test connection, approve, cancel).
Tests use a fake transport and in-memory storage and never call the network.
