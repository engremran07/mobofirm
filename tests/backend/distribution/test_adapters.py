"""Adapters: registry, credential validation, request building per platform, multi-step
flows, token refresh, connection checks, dry-run previews. Nothing touches the network."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from apps.distribution import channels, platforms, services
from apps.distribution.channels import MediaRefs, SendContext
from apps.distribution.constants import MEDIA_IMAGE, MEDIA_TEXT, MEDIA_VIDEO
from apps.distribution.models import Channel, ShareJob
from apps.site_settings.crypto import decrypt_secret

pytestmark = pytest.mark.django_db
SITE = "https://mobofirm.example"
LINK = f"{SITE}/go/abc2345/"
IMAGE = f"{SITE}/media/distribution/card/x.png"
VIDEO = f"{SITE}/media/distribution/video/x.mp4"


def make_ctx(channel, fetcher, *, text=None, media=None, **job):
    stored = services.stored_values(channel)
    secrets = [stored.get(f.name, "") for f in channel.adapter.secret_fields()]
    defaults = {
        "channel": channel,
        "content_kind": "firmware",
        "idempotency_key": "key-1",
        "title": "Acme Phone One Global",
        "url": LINK,
        "text": text or f"Looking for Acme firmware?\n{LINK}\n#Acme #Firmware",
    }
    return SendContext(
        channel,
        ShareJob(**{**defaults, **job}),
        stored,
        channels.Http(fetcher, secrets),
        media or MediaRefs(image_url=IMAGE, video_url=VIDEO),
    )


def publish(channel, fetcher, **kwargs):
    ctx = make_ctx(channel, fetcher, **kwargs)
    return channel.adapter.publish(ctx), ctx


def form(call):
    return {k: v[0] for k, v in parse_qs(call["body"].decode()).items()}


def adapter_for(kind: str) -> channels.Adapter:
    found = channels.get_adapter(kind)
    assert found is not None, kind
    return found


# ------------------------------------------------------------- registry ----


def test_every_platform_choice_has_exactly_one_adapter():
    kinds = {value for value, _label in Channel.Kind.choices}
    assert kinds == set(channels.adapters())
    assert len(kinds) == 16


def test_each_adapter_declares_requirements_and_guidance():
    for kind, adapter in channels.adapters().items():
        assert adapter.label and adapter.how_to, kind
        assert adapter.media in {MEDIA_TEXT, MEDIA_IMAGE, MEDIA_VIDEO}, kind
        assert adapter.message_limit > 0 and adapter.hashtag_limit >= 0, kind
        assert adapter.fields, kind
        for spec in adapter.fields:
            assert spec.store in {"secret", "option", "target", "endpoint_url"}
            assert spec.label and (spec.help or not spec.required), (kind, spec.name)
        assert adapter.secret_fields() or kind == "webhook"


def test_media_requirements_match_the_platform_rules():
    expected = {
        "youtube": MEDIA_VIDEO,
        "tiktok": MEDIA_VIDEO,
        "instagram": MEDIA_IMAGE,
        "pinterest": MEDIA_IMAGE,
        "telegram": MEDIA_TEXT,
        "x": MEDIA_TEXT,
    }
    for kind, media in expected.items():
        assert adapter_for(kind).media == media
    assert adapter_for("telegram").image_optional
    assert adapter_for("x").url_length == 23


@pytest.mark.parametrize("kind", sorted(channels.adapters()))
def test_credentials_are_required_and_complete_values_pass(
    kind, make_channel, default_values
):
    assert Channel(kind=kind, name="empty").configuration_problem
    complete = make_channel(kind)
    assert complete.configuration_problem == ""
    assert complete.state == "ready"
    required = [
        f.name
        for f in complete.adapter.fields
        if f.required and default_values[kind].get(f.name)
    ]
    for name in required:
        partial = make_channel(
            kind, values={name: ""}, name=f"{kind}-partial-{name}", enabled=False
        )
        assert partial.configuration_problem, name


def test_clearing_credentials_makes_the_channel_incomplete(make_channel):
    channel = make_channel("mastodon")
    services.apply_values(channel, {}, clear_secrets=True)
    assert "Access token" in channel.configuration_problem


def test_single_secret_platforms_keep_the_legacy_plain_layout(make_channel):
    telegram = make_channel("telegram")
    assert decrypt_secret(telegram.secret_encrypted) == "123:BOT-TOKEN"
    x = make_channel("x", values={"refresh_token": "r-1234", "client_id": "cid-1234"})
    packed = json.loads(decrypt_secret(x.secret_encrypted))
    assert (
        packed["access_token"] == "x-user-token" and packed["refresh_token"] == "r-1234"
    )
    assert "x-user-token" not in x.secret_encrypted


def test_a_blank_secret_keeps_the_stored_one(make_channel):
    channel = make_channel("telegram")
    services.apply_values(channel, {"chat_id": "@other", "bot_token": ""})
    assert channel.target == "@other"
    assert services.stored_values(channel)["bot_token"] == "123:BOT-TOKEN"


# -------------------------------------------------------- simple requests ----


def test_webhook_is_signed_only_with_a_secret(make_channel):
    plain = make_channel("webhook")
    job = ShareJob(
        content_kind="firmware",
        idempotency_key="1:firmware:x",
        text="hi",
        title="t",
        url="u",
    )
    request = channels.build_request(plain, job, "")
    assert "X-Distribution-Signature" not in request.headers
    signed = channels.build_request(plain, job, '{"signing_secret":"s3cret"}')
    assert signed.headers["X-Distribution-Signature"].startswith("sha256=")
    assert signed.headers["X-Idempotency-Key"] == "1:firmware:x"
    assert json.loads(signed.body)["text"] == "hi"
    insecure = Channel(
        name="h", kind="webhook", endpoint_url="http://hooks.example.com"
    )
    with pytest.raises(channels.ChannelConfigError):
        channels.build_request(insecure, job, "")


def test_telegram_sends_a_photo_only_when_the_caption_fits(make_channel):
    channel = make_channel("telegram")
    adapter = channel.adapter
    values = services.stored_values(channel)
    short = ShareJob(text="short", title="t")
    request = adapter.request(channel, short, values, MediaRefs(image_url=IMAGE))
    assert request.url.endswith("/sendPhoto")
    assert json.loads(request.body) == {
        "chat_id": "@mobofirm",
        "photo": IMAGE,
        "caption": "short",
    }
    plain = adapter.request(channel, short, values, None)
    assert plain.url.endswith("/sendMessage")
    assert json.loads(plain.body) == {"chat_id": "@mobofirm", "text": "short"}
    assert plain.allowed_hosts == {"api.telegram.org"}
    long = ShareJob(text="x" * 2000, title="t")
    assert adapter.request(
        channel, long, values, MediaRefs(image_url=IMAGE)
    ).url.endswith("/sendMessage")


def test_discord_never_pings_and_embeds_the_card(make_channel):
    channel = make_channel("discord")
    adapter = channel.adapter
    values = services.stored_values(channel)
    request = adapter.request(
        channel, ShareJob(text="hello"), values, MediaRefs(image_url=IMAGE)
    )
    body = json.loads(request.body)
    assert request.url.endswith("/api/webhooks/1/abc?wait=true")
    assert body["allowed_mentions"] == {"parse": []}
    assert body["embeds"] == [{"image": {"url": IMAGE}}]
    for bad in ("https://evil.example/api/webhooks/1/a", "https://discord.com/other"):
        with pytest.raises(channels.ChannelConfigError):
            adapter.request(channel, ShareJob(text="x"), {"webhook_url": bad})


def test_slack_accepts_only_slack_webhooks(make_channel):
    channel = make_channel("slack")
    request = channel.adapter.request(
        channel, ShareJob(text="hi"), services.stored_values(channel)
    )
    assert json.loads(request.body) == {"text": "hi", "link_names": 0}
    assert request.allowed_hosts == {"hooks.slack.com"}
    bad = make_channel(
        "slack",
        values={"webhook_url": "https://evil.example/services/x"},
        name="bad-slack",
        enabled=False,
    )
    assert "hooks.slack.com" in bad.configuration_problem


# ------------------------------------------------------------ single post ----


def test_x_refreshes_its_token_and_persists_the_rotated_refresh_token(
    make_channel, make_content, net
):
    make_channel(
        "x",
        values={
            "refresh_token": "old-refresh-1",
            "client_id": "cid-1234",
            "client_secret": "csecret-1234",
        },
    )
    services.enqueue(make_content())
    fetcher = net.Fetcher(
        routes={
            "oauth2/token": net.ok(
                {"access_token": "new-access", "refresh_token": "rotated-refresh"}
            ),
            "/2/tweets": net.ok({"data": {"id": "t1"}}),
        }
    )
    assert services.process_due_jobs(fetcher=fetcher).sent == 1
    token_call, tweet_call = fetcher.calls
    pair = base64.b64encode(b"cid-1234:csecret-1234").decode()
    assert token_call["headers"]["Authorization"] == f"Basic {pair}"
    assert token_call["allowed_hosts"] == {"api.x.com"}
    assert tweet_call["headers"]["Authorization"] == "Bearer new-access"
    assert ShareJob.objects.get().external_id == "t1"
    channel = Channel.objects.get()
    assert services.stored_values(channel)["refresh_token"] == "rotated-refresh"
    assert "rotated-refresh" not in channel.secret_encrypted


def test_x_uses_the_access_token_directly_without_a_refresh_token(make_channel, net):
    channel = make_channel("x")
    fetcher = net.Fetcher(net.ok({"data": {"id": "t2"}}))
    outcome, _ctx = publish(channel, fetcher)
    assert outcome.ok and outcome.external_id == "t2"
    call = fetcher.calls[0]
    assert call["url"] == "https://api.x.com/2/tweets"
    assert call["headers"]["Authorization"] == "Bearer x-user-token"
    assert json.loads(call["body"])["text"].startswith("Looking for Acme")


def test_mastodon_posts_to_the_configured_server_with_an_idempotency_key(
    make_channel, net
):
    channel = make_channel("mastodon")
    fetcher = net.Fetcher(net.ok({"id": "109"}))
    outcome, _ctx = publish(channel, fetcher)
    call = fetcher.calls[0]
    assert outcome.ok and outcome.external_id == "109"
    assert call["url"] == "https://mastodon.example/api/v1/statuses"
    assert call["allowed_hosts"] == {"mastodon.example"}
    assert call["headers"]["Idempotency-Key"] == "key-1"
    assert json.loads(call["body"])["visibility"] == "public"
    insecure = make_channel(
        "mastodon",
        values={"instance_url": "http://mastodon.example"},
        name="m2",
        enabled=False,
    )
    assert "https" in insecure.configuration_problem


def test_bluesky_signs_in_then_creates_a_record_with_byte_correct_facets(
    make_channel, net
):
    channel = make_channel("bluesky")
    text = "Café firmware https://mobofirm.example/go/abc2345/ #Acme"
    fetcher = net.Fetcher(
        net.ok({"accessJwt": "jwt-secret-token", "did": "did:plc:1"}),
        net.ok({"uri": "at://did:plc:1/app.bsky.feed.post/3k"}),
    )
    outcome, _ctx = publish(channel, fetcher, text=text)
    assert outcome.ok and outcome.external_id.startswith("at://")
    session, record = fetcher.calls
    assert session["url"].endswith("com.atproto.server.createSession")
    assert json.loads(session["body"]) == {
        "identifier": "mobofirm.bsky.social",
        "password": "abcd-efgh-ijkl-mnop",
    }
    assert record["headers"]["Authorization"] == "Bearer jwt-secret-token"
    body = json.loads(record["body"])
    assert body["repo"] == "did:plc:1" and body["collection"] == "app.bsky.feed.post"
    raw = text.encode()
    link, tag = body["record"]["facets"]
    url = "https://mobofirm.example/go/abc2345/"
    assert link["index"]["byteStart"] == raw.index(url.encode())
    assert link["index"]["byteEnd"] - link["index"]["byteStart"] == len(url.encode())
    assert tag["features"][0]["tag"] == "Acme"
    assert body["record"]["embed"]["external"]["uri"] == LINK


def test_facebook_posts_text_or_a_photo(make_channel, net):
    channel = make_channel("facebook")
    fetcher = net.Fetcher(net.ok({"id": "1_2"}))
    outcome, _ctx = publish(channel, fetcher, media=MediaRefs())
    call = fetcher.calls[0]
    assert outcome.ok
    assert call["url"] == "https://graph.facebook.com/v21.0/12345/feed"
    assert call["headers"]["Authorization"] == "Bearer fb-page-token"
    assert form(call)["link"] == LINK
    photo_fetcher = net.Fetcher(net.ok({"post_id": "1_9", "id": "9"}))
    outcome, _ctx = publish(channel, photo_fetcher)
    photo = photo_fetcher.calls[0]
    assert photo["url"].endswith("/12345/photos") and form(photo)["url"] == IMAGE
    assert outcome.external_id == "1_9"


def test_linkedin_posts_an_article_share_with_versioned_headers(make_channel, net):
    channel = make_channel("linkedin")
    fetcher = net.Fetcher(net.ok(b"", status=201, **{"x-restli-id": "urn:li:share:1"}))
    outcome, _ctx = publish(channel, fetcher)
    call = fetcher.calls[0]
    body = json.loads(call["body"])
    assert outcome.ok and outcome.external_id == "urn:li:share:1"
    assert call["url"] == "https://api.linkedin.com/rest/posts"
    assert call["headers"]["X-Restli-Protocol-Version"] == "2.0.0"
    assert call["headers"]["LinkedIn-Version"] == platforms.LINKEDIN_VERSION
    assert body["author"] == "urn:li:organization:123"
    assert body["content"]["article"]["source"] == LINK
    assert body["visibility"] == "PUBLIC"


def test_pinterest_creates_a_pin_from_the_image(make_channel, net):
    channel = make_channel("pinterest")
    fetcher = net.Fetcher(net.ok({"id": "pin1"}, status=201))
    outcome, _ctx = publish(channel, fetcher)
    body = json.loads(fetcher.calls[0]["body"])
    assert outcome.ok and outcome.external_id == "pin1"
    assert body["board_id"] == "555" and body["link"] == LINK
    assert body["media_source"] == {"source_type": "image_url", "url": IMAGE}
    assert len(body["title"]) <= 100


# ---------------------------------------------------------------- reddit ----


def test_reddit_exchanges_the_refresh_token_then_submits_a_link(make_channel, net):
    channel = make_channel("reddit")
    fetcher = net.Fetcher(
        net.ok({"access_token": "reddit-access"}),
        net.ok({"json": {"errors": [], "data": {"name": "t3_abc"}}}),
    )
    outcome, _ctx = publish(channel, fetcher)
    token, submit = fetcher.calls
    assert outcome.ok and outcome.external_id == "t3_abc"
    assert token["url"] == "https://www.reddit.com/api/v1/access_token"
    assert form(token) == {
        "grant_type": "refresh_token",
        "refresh_token": "rrefresh-1234",
    }
    assert "mobofirm" in token["headers"]["User-Agent"]
    fields = form(submit)
    assert submit["url"] == "https://oauth.reddit.com/api/submit"
    assert fields["sr"] == "androidflashing" and fields["kind"] == "link"
    assert fields["url"] == LINK and fields["title"] == "Acme Phone One Global"
    assert submit["headers"]["Authorization"] == "Bearer reddit-access"


def test_reddit_errors_in_a_200_response_are_not_success(make_channel, net):
    channel = make_channel("reddit")
    token = net.ok({"access_token": "reddit-access"})
    gone = net.ok({"json": {"errors": [["SUBREDDIT_NOEXIST", "no such sub", "sr"]]}})
    outcome, _ctx = publish(channel, net.Fetcher(token, gone))
    assert not outcome.ok and not outcome.retryable
    slow = net.ok({"json": {"errors": [["RATELIMIT", "slow down", "ratelimit"]]}})
    outcome, _ctx = publish(channel, net.Fetcher(token, slow))
    assert not outcome.ok and outcome.retryable and outcome.retry_after >= 600


# ------------------------------------------------------- multi-step state ----


def test_instagram_feed_creates_a_container_then_publishes(make_channel, net):
    channel = make_channel("instagram")
    fetcher = net.Fetcher(net.ok({"id": "c1"}), net.ok({"id": "m1"}))
    outcome, ctx = publish(channel, fetcher)
    create, finish = fetcher.calls
    assert outcome.ok and outcome.external_id == "m1"
    assert create["url"] == "https://graph.facebook.com/v21.0/1784/media"
    assert form(create)["image_url"] == IMAGE
    assert form(finish) == {"creation_id": "c1"}
    assert ctx.state["creation_id"] == "c1"


def test_instagram_reel_waits_for_processing_and_never_recreates_the_container(
    make_channel, net
):
    channel = make_channel("instagram", values={"format": "reel"})
    ctx = make_ctx(
        channel,
        net.Fetcher(net.ok({"id": "c7"}), net.ok({"status_code": "IN_PROGRESS"})),
    )
    waiting = channel.adapter.publish(ctx)
    assert not waiting.ok and waiting.retryable and waiting.code == "processing"
    assert form(ctx.http.fetcher.calls[0])["media_type"] == "REELS"
    assert form(ctx.http.fetcher.calls[0])["video_url"] == VIDEO
    assert ctx.state == {"creation_id": "c7"}
    retry = net.Fetcher(net.ok({"status_code": "FINISHED"}), net.ok({"id": "m7"}))
    ctx.http.fetcher = retry
    done = channel.adapter.publish(ctx)
    assert done.ok and done.external_id == "m7"
    assert len(retry.calls) == 2 and "/media_publish" in retry.calls[1]["url"]


def test_instagram_reel_rejected_by_the_platform_is_terminal(make_channel, net):
    channel = make_channel("instagram", values={"format": "reel"})
    fetcher = net.Fetcher(net.ok({"id": "c8"}), net.ok({"status_code": "ERROR"}))
    outcome, ctx = publish(channel, fetcher)
    assert not outcome.ok and not outcome.retryable
    assert "creation_id" not in ctx.state


def test_threads_resumes_at_the_publish_step_after_a_failure(make_channel, net):
    channel = make_channel("threads")
    ctx = make_ctx(channel, net.Fetcher(net.ok({"id": "c9"}), net.reply(500)))
    first = channel.adapter.publish(ctx)
    assert not first.ok and first.retryable
    assert ctx.state == {"creation_id": "c9"}
    resume = net.Fetcher(net.ok({"id": "th1"}))
    ctx.http.fetcher = resume
    second = channel.adapter.publish(ctx)
    assert second.ok and second.external_id == "th1"
    assert len(resume.calls) == 1 and "/threads_publish" in resume.calls[0]["url"]
    assert resume.calls[0]["url"].startswith("https://graph.threads.net/v1.0/99/")


def test_whatsapp_sends_the_template_to_each_opted_in_number_once(make_channel, net):
    channel = make_channel("whatsapp")
    ctx = make_ctx(channel, net.Fetcher(net.ok({"messages": [{}]}), net.reply(500)))
    first = channel.adapter.publish(ctx)
    assert not first.ok and first.retryable
    assert ctx.state["sent_to"] == ["+15551234567"]
    body = json.loads(ctx.http.fetcher.calls[0]["body"])
    assert body["template"]["name"] == "news_update"
    assert body["template"]["components"][0]["parameters"][1]["text"] == LINK
    resume = net.Fetcher(net.ok({"messages": [{}]}))
    ctx.http.fetcher = resume
    assert channel.adapter.publish(ctx).ok
    assert [json.loads(c["body"])["to"] for c in resume.calls] == ["+4915112345678"]


def test_whatsapp_needs_valid_recipients(make_channel):
    channel = make_channel(
        "whatsapp", values={"recipients": "not a number"}, name="wa2", enabled=False
    )
    assert "recipient" in channel.configuration_problem


# ----------------------------------------------------------------- video ----


def test_tiktok_pulls_the_video_url_and_defaults_to_private(make_channel, net):
    channel = make_channel("tiktok", values={"privacy_level": ""})
    fetcher = net.Fetcher(
        net.ok({"data": {"publish_id": "p1"}, "error": {"code": "ok"}})
    )
    outcome, _ctx = publish(channel, fetcher)
    body = json.loads(fetcher.calls[0]["body"])
    assert outcome.ok and outcome.external_id == "p1"
    assert body["source_info"] == {"source": "PULL_FROM_URL", "video_url": VIDEO}
    assert body["post_info"]["privacy_level"] == "SELF_ONLY"
    refusal = net.ok(
        {"data": {}, "error": {"code": "spam_risk", "message": "too many posts"}}
    )
    outcome, _ctx = publish(channel, net.Fetcher(refusal))
    assert (
        not outcome.ok and not outcome.retryable and "too many posts" in outcome.detail
    )


def test_youtube_uploads_the_generated_video_as_a_short(make_channel, net):
    channel = make_channel("youtube")
    media = MediaRefs(
        video_url=VIDEO, video_name="v.mp4", reader=lambda name: b"VIDEOBYTES"
    )
    fetcher = net.Fetcher(net.ok({"access_token": "yt-access"}), net.ok({"id": "vid1"}))
    outcome, _ctx = publish(channel, fetcher, media=media)
    token, upload = fetcher.calls
    assert outcome.ok and outcome.external_id == "vid1"
    assert token["url"] == "https://oauth2.googleapis.com/token"
    assert form(token)["client_id"] == "yid-client"
    assert upload["url"].startswith(
        "https://www.googleapis.com/upload/youtube/v3/videos"
    )
    assert upload["headers"]["Authorization"] == "Bearer yt-access"
    assert upload["headers"]["Content-Type"].startswith("multipart/related; boundary=")
    assert b"VIDEOBYTES" in upload["body"] and b"#Shorts" in upload["body"]


def test_youtube_without_a_video_never_calls_the_api(make_channel, net):
    channel = make_channel("youtube")
    fetcher = net.Fetcher()
    outcome, _ctx = publish(channel, fetcher, media=MediaRefs())
    assert not outcome.ok and outcome.code == "media" and fetcher.calls == []


def test_youtube_refuses_oversized_uploads(make_channel, net, monkeypatch):
    monkeypatch.setattr(platforms, "MAX_UPLOAD_BYTES", 5)
    channel = make_channel("youtube")
    media = MediaRefs(
        video_url=VIDEO, video_name="v.mp4", reader=lambda name: b"x" * 10
    )
    fetcher = net.Fetcher()
    outcome, _ctx = publish(channel, fetcher, media=media)
    assert not outcome.ok and "too large" in outcome.detail and fetcher.calls == []


# ------------------------------------------------------- media requirement ----


def test_media_problems_name_what_is_missing(make_channel):
    for kind, media, fragment in (
        ("instagram", MediaRefs(), "image"),
        ("pinterest", MediaRefs(), "image"),
        ("youtube", MediaRefs(image_url=IMAGE), "video"),
        ("tiktok", MediaRefs(image_url=IMAGE), "video"),
    ):
        adapter = make_channel(kind).adapter
        assert fragment in adapter.media_problem(media).lower(), kind
    assert adapter_for("x").media_problem(MediaRefs()) == ""
    reel = make_channel("instagram", values={"format": "reel"}, name="ig-reel")
    values = services.stored_values(reel)
    assert reel.adapter.required_media(values) == MEDIA_VIDEO
    assert "video" in reel.adapter.media_problem(MediaRefs(image_url=IMAGE), values)


# ------------------------------------------------------ connection checks ----

CHECKS: dict[str, tuple[dict[str, dict[str, Any]], str]] = {
    "telegram": (
        {"getMe": {"ok": True, "result": {"username": "mobo_bot"}}},
        "@mobo_bot",
    ),
    "discord": ({"": {"id": "1"}}, "valid"),
    "x": ({"": {"data": {"username": "mobofirm"}}}, "@mobofirm"),
    "mastodon": ({"": {"acct": "mobofirm"}}, "@mobofirm"),
    "bluesky": ({"": {"accessJwt": "jwt-tok-123", "did": "did:plc:1"}}, "did:plc:1"),
    "facebook": ({"": {"name": "Mobofirm Page"}}, "Mobofirm Page"),
    "instagram": ({"": {"username": "mobo"}}, "@mobo"),
    "threads": ({"": {"username": "mobo"}}, "@mobo"),
    "pinterest": ({"": {"username": "mobo"}}, "mobo"),
    "tiktok": ({"": {"data": {}}}, "reachable"),
    "whatsapp": ({"": {"display_phone_number": "+1 555"}}, "+1 555"),
    "webhook": ({"": {}}, ""),
    "reddit": (
        {"api/v1/me": {"name": "mobo"}, "": {"access_token": "reddit-access"}},
        "u/mobo",
    ),
    "youtube": (
        {
            "youtube/v3/channels": {"items": [{"snippet": {"title": "Mobo TV"}}]},
            "": {"access_token": "yt-access"},
        },
        "Mobo TV",
    ),
}


@pytest.mark.parametrize("kind", sorted(CHECKS))
def test_test_connection_succeeds_with_a_read_only_call(kind, make_channel, net):
    routes, expected = CHECKS[kind]
    channel = make_channel(kind)
    fetcher = net.Fetcher(routes={k: net.ok(v) for k, v in routes.items()})
    outcome = services.check_connection(channel, fetcher=fetcher)
    channel.refresh_from_db()
    assert outcome.ok, outcome
    assert channel.connection_ok is True and expected in channel.connection_message
    assert channel.connection_checked_at is not None
    assert all(c["method"] in {"GET", "POST"} for c in fetcher.calls)
    if kind != "webhook":
        assert not any(b"title" in (c["body"] or b"") for c in fetcher.calls)


def test_a_failed_connection_test_is_recorded_without_secrets(make_channel, net):
    channel = make_channel("telegram")
    fetcher = net.Fetcher(net.ok({"description": "bad 123:BOT-TOKEN"}, status=401))
    outcome = services.check_connection(channel, fetcher=fetcher)
    channel.refresh_from_db()
    assert not outcome.ok and channel.connection_ok is False
    assert "401" in channel.connection_message
    assert "BOT-TOKEN" not in channel.connection_message


@pytest.mark.parametrize("kind", ["slack", "linkedin"])
def test_platforms_without_a_read_only_check_say_so(kind, make_channel, net):
    channel = make_channel(kind)
    fetcher = net.Fetcher()
    outcome = services.check_connection(channel, fetcher=fetcher)
    channel.refresh_from_db()
    assert outcome.code == "unsupported" and fetcher.calls == []
    assert channel.connection_ok is None
    assert "Send test post" in channel.connection_message


def test_an_incomplete_channel_fails_the_connection_test_locally(net):
    channel = Channel.objects.create(name="bare", kind="mastodon")
    fetcher = net.Fetcher()
    outcome = services.check_connection(channel, fetcher=fetcher)
    assert not outcome.ok and outcome.code == "config" and fetcher.calls == []


# ---------------------------------------------------------------- preview ----


def test_preview_is_a_dry_run_that_flags_limits_and_media(make_channel):
    channel = make_channel("x")
    values = services.stored_values(channel)
    preview = channel.adapter.preview("x" * 400, values, MediaRefs())
    assert preview.length == 400 and preview.limit == 280 and not preview.sendable
    assert "limit is 280" in preview.warnings[0]
    assert preview.steps == ("POST api.x.com/2/tweets",)
    insta = make_channel("instagram")
    bare = insta.adapter.preview("hi", services.stored_values(insta), MediaRefs())
    assert bare.requires == MEDIA_IMAGE and "image" in bare.media_problem.lower()


def test_x_counts_every_link_as_23_characters(make_channel):
    adapter = make_channel("x").adapter
    text = "hello https://mobofirm.example/go/abc2345/very/long/path/that/would/not/fit"
    assert channels.visible_length(text, adapter.url_length) == len("hello ") + 23


def test_build_preview_composes_a_sample_post(make_channel):
    channel = make_channel("mastodon", use_composer=True)
    result = services.build_preview(channel)
    assert result is not None and result.sample
    assert "Acme" in result.preview.text and result.preview.sendable
    assert result.link.startswith(f"{SITE}/go/")
