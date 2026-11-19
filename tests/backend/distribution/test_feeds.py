"""Public feeds (RSS, Atom, JSON), visibility, conditional GET, autodiscovery, the staff
Feeds page, and the WebSub / IndexNow outbox."""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from xml.etree import ElementTree as ET

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from apps.distribution import feeds, news, syndication
from apps.distribution.content import Content
from apps.distribution.models import (
    DistributionSettings,
    FeedConfig,
    FeedStat,
    PingOutbox,
    ShareLog,
)

pytestmark = pytest.mark.django_db
HUB = "https://hub.example.com/"
ATOM = "{http://www.w3.org/2005/Atom}"


def url(key, fmt):
    return reverse("feeds:feed", kwargs={"key": key, "fmt": fmt})


# ------------------------------------------------------------ validity ----


def test_rss_is_valid_and_complete(client, make_post):
    post = make_post()
    response = client.get(url("latest", "rss"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/rss+xml")
    body = response.content
    assert feeds.validate(body, "rss") == []
    channel = ET.fromstring(body).find("channel")
    assert channel is not None
    for tag in ("title", "link", "description", "language", "lastBuildDate", "ttl"):
        assert channel.findtext(tag)
    self_link = channel.find(f"{ATOM}link")
    assert self_link is not None and self_link.get("rel") == "self"
    node = channel.find("item")
    assert node is not None
    guid = node.find("guid")
    assert guid is not None and guid.get("isPermaLink") == "true"
    assert guid.text.endswith(post.get_absolute_url())
    assert "FULL-BODY-TEXT" in body.decode()  # content:encoded
    assert node.find("{http://purl.org/dc/elements/1.1/}creator") is not None


def test_atom_and_json_are_valid(client, make_post):
    make_post()
    atom = client.get(url("latest", "atom"))
    assert feeds.validate(atom.content, "atom") == []
    assert ET.fromstring(atom.content).find(f"{ATOM}entry") is not None
    data = client.get(url("latest", "json")).json()
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert feeds.validate(json.dumps(data).encode(), "json") == []
    assert data["items"][0]["content_html"]


def test_hub_link_only_when_configured(client, make_post):
    make_post()
    assert 'rel="hub"' not in client.get(url("latest", "rss")).content.decode()
    cfg = DistributionSettings.get_solo()
    cfg.websub_hub_url = HUB
    cfg.save()
    rss = client.get(url("latest", "rss")).content.decode()
    assert f'href="{HUB}"' in rss and 'rel="hub"' in rss
    assert client.get(url("latest", "json")).json()["hubs"] == [
        {"type": "WebSub", "url": HUB}
    ]


def test_validator_flags_broken_feeds():
    assert feeds.validate(b"<rss", "rss")
    assert feeds.validate(b"<rss version='2.0'><channel></channel></rss>", "rss")
    assert feeds.validate(b"{}", "json")


# ------------------------------------------------------------- visibility ----


def test_only_public_content_is_listed(client, make_post, make_firmware):
    from apps.blog.models import PostStatus

    make_post("Visible post title that is long enough")
    make_post(
        "Draft post must stay hidden entirely", status=PostStatus.DRAFT, publish_at=None
    )
    make_post(
        "Scheduled post must stay hidden entirely",
        publish_at=timezone.now() + timedelta(days=3),
    )
    make_firmware("ok.zip")
    make_firmware("pending.zip", verified=False, model="Hidden Pending")
    body = client.get(url("latest", "rss")).content.decode()
    assert "Visible post title" in body and "Atlas One firmware" in body
    assert "Draft post" not in body and "Scheduled post" not in body
    assert "Hidden Pending" not in body
    assert "stored_file_path" not in body and "/firmware/ok.zip" not in body


def test_gated_posts_share_only_a_teaser(client, make_post):
    from apps.blog.models import PostAccess

    make_post(
        "Paid deep dive for members only",
        access=PostAccess.FEATURED,
        body="<p>SECRET-PAID-BODY</p>",
    )
    body = client.get(url("latest", "rss")).content.decode()
    assert "SECRET-PAID-BODY" not in body and "Read the full article" in body


def test_section_brand_and_tag_feeds(client, make_post, make_firmware):
    make_post()
    make_firmware()
    blog = client.get(url("section/blog", "rss")).content.decode()
    fw = client.get(url("section/firmware", "rss")).content.decode()
    assert "A public journal post" in blog and "Atlas One" not in blog
    assert "Atlas One" in fw and "A public journal post" not in fw
    assert client.get(url("brand/northstar", "atom")).status_code == 200
    assert client.get(url("brand/unknown", "atom")).status_code == 404
    assert client.get(url("tag/nothing-here", "json")).status_code == 404


def test_unknown_or_invalid_feeds_are_404(client):
    assert client.get("/feeds/..%2Fx.rss").status_code == 404
    assert client.get("/feeds/section/other.rss").status_code == 404
    assert client.get("/feeds/latest.txt").status_code == 404


def test_feeds_can_be_switched_off(client, make_post):
    make_post()
    config, _ = FeedConfig.objects.get_or_create(key="latest")
    config.enabled = False
    config.save()
    assert client.get(url("latest", "rss")).status_code == 404
    assert client.get(url("section/blog", "rss")).status_code == 200
    cfg = DistributionSettings.get_solo()
    cfg.feeds_enabled = False
    cfg.save()
    assert client.get(url("section/blog", "rss")).status_code == 404
    assert client.get(reverse("feeds:index")).status_code == 404


# ---------------------------------------------------- conditional + gzip ----


def test_etag_last_modified_and_304(client, make_post):
    make_post()
    first = client.get(url("latest", "rss"))
    etag, modified = first["ETag"], first["Last-Modified"]
    assert first["Cache-Control"].startswith("public")
    assert client.get(url("latest", "rss"), HTTP_IF_NONE_MATCH=etag).status_code == 304
    assert (
        client.get(url("latest", "rss"), HTTP_IF_MODIFIED_SINCE=modified).status_code
        == 304
    )
    changed = client.get(url("latest", "rss"), HTTP_IF_NONE_MATCH='W/"other"')
    assert changed.status_code == 200
    make_post("Another brand new journal post here")
    assert client.get(url("latest", "rss"), HTTP_IF_NONE_MATCH=etag).status_code == 200


def test_gzip_when_accepted(client, make_post):
    for index in range(8):
        make_post(f"Journal post number {index} with a long title")
    plain = client.get(url("latest", "rss")).content
    zipped = client.get(url("latest", "rss"), HTTP_ACCEPT_ENCODING="gzip")
    assert zipped["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in zipped["Vary"]
    assert gzip.decompress(zipped.content) == plain


def test_fetches_are_counted_without_personal_data(client, make_post):
    make_post()
    first = client.get(url("latest", "rss"))
    client.get(url("latest", "rss"), HTTP_IF_NONE_MATCH=first["ETag"])
    config = FeedConfig.objects.get(key="latest")
    assert config.fetch_count == 2 and config.not_modified_count == 1
    assert config.last_item_count == 1 and config.last_size > 0
    stat = FeedStat.objects.get(feed_key="latest")
    assert stat.fetches == 2 and stat.not_modified == 1
    assert {f.name for f in FeedStat._meta.fields} == {
        "id",
        "feed_key",
        "bucket",
        "fetches",
        "not_modified",
    }


def test_internal_crawl_is_not_counted(client, make_post):
    from apps.core.internal_request import internal_crawl_environ

    make_post()
    client.get(url("latest", "rss"), **internal_crawl_environ())
    assert not FeedConfig.objects.exists() and not FeedStat.objects.exists()


# ------------------------------------------------------- autodiscovery ----


def test_public_head_advertises_the_feeds(client):
    html = client.get("/").content.decode()
    assert 'rel="alternate" type="application/rss+xml"' in html
    assert (
        'type="application/atom+xml"' in html and 'type="application/feed+json"' in html
    )
    cfg = DistributionSettings.get_solo()
    cfg.feeds_enabled = False
    cfg.save()
    cache.clear()
    assert "application/rss+xml" not in client.get("/").content.decode()


def test_index_page_lists_the_feeds(client):
    page = client.get(reverse("feeds:index"))
    assert page.status_code == 200
    body = page.content.decode()
    assert url("latest", "rss") in body
    assert (
        "/feeds/google-news.rss" not in body
    )  # opt-in: hidden until the news sitemap is on


# ----------------------------------------------------------- google news ----


def test_news_feed_is_opt_in_and_limited_to_the_last_48_hours(client, make_post):
    from apps.seo.models import SEOSettings

    make_post("Fresh news article about a launch today")
    assert client.get(url("google-news", "rss")).status_code == 404
    row = SEOSettings.get_solo()
    row.seo_enabled = True
    row.sitemap_enabled = True
    row.sitemap_news_enabled = True
    row.save()
    status = news.status()
    assert status["active"] and status["rss_url"].endswith("/feeds/google-news.rss")
    assert not status["registered"]
    old = make_post(
        "Old article from last week stays out of news",
        publish_at=timezone.now() - timedelta(days=5),
        published_at=timezone.now() - timedelta(days=5),
    )
    response = client.get(url("google-news", "rss"))
    body = response.content.decode()
    assert response.status_code == 200 and feeds.validate(response.content, "rss") == []
    assert old.title not in body
    assert "Fresh news article" in body and "FULL-BODY-TEXT" in body  # full text


def test_news_checklist_is_manual_and_persistent():
    assert not any(step["done"] for step in news.checklist())
    assert news.set_step("publisher_center", True)
    assert not news.set_step("nonsense", True)
    assert [s["done"] for s in news.checklist()] == [True, False, False]


def test_discover_scan_reports_what_is_missing(make_post):
    make_post("Short")
    check = news.discover_scan()[0]
    assert not check.ready
    assert any("Title" in p for p in check.problems) and any(
        "cover image" in p for p in check.problems
    )


# --------------------------------------------------------- websub outbox ----


def item(ref="post:1"):
    return Content(
        kind="blog",
        object_ref=ref,
        title="T",
        url="https://mobofirm.example/blog/t/",
        tags=("Android 14",),
    )


class Reply:
    def __init__(self, status):
        self.status = status
        self.headers = {}
        self.body = b""


class Hub:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def fetch(self, target, **kwargs):
        self.calls.append((target, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return Reply(outcome)


def test_nothing_is_queued_until_a_hub_is_configured():
    assert syndication.announce(item()) == 0
    assert not PingOutbox.objects.exists()


@pytest.mark.usefixtures("hub_on")
def test_announce_is_idempotent_per_entry_and_feed():
    first = syndication.announce(item())
    assert first >= 6  # latest + blog + tag, three formats each
    assert syndication.announce(item()) == 0
    assert PingOutbox.objects.count() == first
    assert syndication.announce(item("post:2")) == first
    urls = set(PingOutbox.objects.values_list("url", flat=True))
    assert any("/feeds/tag/android-14.rss" in u for u in urls)


@pytest.mark.usefixtures("hub_on")
def test_delivery_posts_hub_publish_and_audits():
    syndication.announce(item())
    fake = Hub(*[204] * PingOutbox.objects.count())
    counts = syndication.process_due(fetcher=fake, limit=100)
    assert counts["sent"] == len(fake.calls) > 0
    target, kwargs = fake.calls[0]
    assert (
        target == HUB
        and kwargs["method"] == "POST"
        and kwargs["follow_redirects"] is False
    )
    assert kwargs["allowed_hosts"] == frozenset({"hub.example.com"})
    assert b"hub.mode=publish" in kwargs["body"]
    assert not PingOutbox.objects.exclude(status="sent").exists()
    assert ShareLog.objects.filter(message__startswith="WebSub hub ping sent").exists()


@pytest.mark.usefixtures("hub_on")
def test_transient_failure_backs_off_then_succeeds_and_terminal_gives_up():
    syndication.announce(item())
    ping = PingOutbox.objects.order_by("id").first()
    now = timezone.now()
    assert syndication.deliver(ping.pk, now=now, fetcher=Hub(503)) == "retried"
    ping.refresh_from_db()
    assert (
        ping.status == "pending" and ping.attempts == 1 and ping.next_attempt_at > now
    )
    assert syndication.deliver(ping.pk, now=now, fetcher=Hub(204)) == ""  # not due yet
    later = ping.next_attempt_at + timedelta(seconds=1)
    assert syndication.deliver(ping.pk, now=later, fetcher=Hub(204)) == "sent"
    other = PingOutbox.objects.exclude(pk=ping.pk).first()
    assert syndication.deliver(other.pk, now=now, fetcher=Hub(400)) == "failed"
    other.refresh_from_db()
    assert other.status == "failed" and "400" in other.last_error
    assert syndication.retry(other) and other.status == "pending"
    assert not syndication.retry(ping)  # sent pings never restart


@pytest.mark.usefixtures("hub_on")
def test_retries_stop_after_the_attempt_limit():
    syndication.announce(item())
    ping = PingOutbox.objects.first()
    now = timezone.now()
    outcome = ""
    for _ in range(syndication.MAX_ATTEMPTS):
        ping.refresh_from_db()
        outcome = syndication.deliver(
            ping.pk,
            now=now + timedelta(days=30 * (ping.attempts + 1)),
            fetcher=Hub(500),
        )
    ping.refresh_from_db()
    assert outcome == "failed" and ping.status == "failed"
    assert ping.attempts == syndication.MAX_ATTEMPTS


@pytest.mark.usefixtures("hub_on")
def test_removing_the_hub_makes_pending_pings_fail_cleanly():
    syndication.announce(item())
    cfg = DistributionSettings.get_solo()
    cfg.websub_hub_url = ""
    cfg.save()
    ping = PingOutbox.objects.first()
    assert syndication.deliver(ping.pk, fetcher=Hub()) == "failed"


@pytest.mark.usefixtures("hub_on")
def test_publish_event_queues_pings_after_commit(
    make_post, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        make_post("Published through the signal with a long title")
    assert PingOutbox.objects.filter(kind="websub").exists()


# ------------------------------------------------------------- staff page ----


def test_feeds_page_needs_permission_and_renders(admin_client, staff_client, make_post):
    make_post()
    page = admin_client.get(reverse("admin:distribution_channel_feeds"))
    body = page.content.decode()
    assert page.status_code == 200
    for text in (
        "Feeds &amp; Google News",
        "Not enabled",
        "Validate",
        "Discover readiness",
        "Setup checklist",
    ):
        assert text in body
    assert (
        staff_client().get(reverse("admin:distribution_channel_feeds")).status_code
        == 403
    )
    assert (
        staff_client("view_channel")
        .get(reverse("admin:distribution_channel_feeds"))
        .status_code
        == 200
    )


def test_live_region_is_a_fragment_over_htmx(admin_client):
    response = admin_client.get(
        reverse("admin:distribution_channel_feeds"),
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="dist-feeds-live",
    )
    body = response.content.decode()
    assert (
        'id="dist-feeds-live"' in body
        and "<html" not in body
        and 'hx-trigger="every 20s"' in body
    )


def test_toggle_validate_and_settings_post_redirect(
    admin_client, staff_client, make_post
):
    make_post()
    page = reverse("admin:distribution_channel_feeds")
    toggled = admin_client.post(
        reverse("admin:distribution_channel_feeds_toggle"), {"key": "latest"}
    )
    assert toggled.status_code == 302 and toggled.url == page
    assert FeedConfig.objects.get(key="latest").enabled is False
    checked = admin_client.post(
        reverse("admin:distribution_channel_feeds_validate"), {"key": "section/blog"}
    )
    assert checked.status_code == 302
    assert (
        FeedConfig.objects.get(key="section/blog").last_validation
        == "Valid in RSS, Atom and JSON."
    )
    saved = admin_client.post(
        reverse("admin:distribution_channel_feeds_settings"),
        {"feeds_enabled": "on", "feed_item_limit": "10", "websub_hub_url": HUB},
    )
    assert saved.status_code == 302
    cfg = DistributionSettings.get_solo()
    assert (
        cfg.websub_hub_url == HUB
        and cfg.feed_item_limit == 10
        and not cfg.feeds_dynamic_enabled
    )
    admin_client.post(
        reverse("admin:distribution_channel_feeds_settings"),
        {
            "feeds_enabled": "on",
            "feed_item_limit": "10",
            "websub_hub_url": "http://insecure.example/",
        },
    )
    assert DistributionSettings.get_solo().websub_hub_url == HUB  # https only
    reader = staff_client("view_channel")
    assert (
        reader.post(
            reverse("admin:distribution_channel_feeds_toggle"), {"key": "latest"}
        ).status_code
        == 403
    )
    assert (
        reader.post(
            reverse("admin:distribution_channel_feeds_settings"), {}
        ).status_code
        == 403
    )
    assert (
        admin_client.get(reverse("admin:distribution_channel_feeds_toggle")).status_code
        == 405
    )
    assert (
        admin_client.post(
            reverse("admin:distribution_channel_feeds_toggle"), {"key": "bogus/x"}
        ).status_code
        == 404
    )


@pytest.mark.usefixtures("hub_on")
def test_outbox_actions_and_news_step(admin_client):
    syndication.announce(item())
    ping = PingOutbox.objects.first()
    cancel = reverse("admin:distribution_channel_feeds_ping", args=[ping.pk, "cancel"])
    assert admin_client.post(cancel).status_code == 302
    ping.refresh_from_db()
    assert ping.status == "cancelled"
    assert (
        admin_client.post(
            reverse("admin:distribution_channel_feeds_ping", args=[ping.pk, "bogus"])
        ).status_code
        == 404
    )
    step = admin_client.post(
        reverse("admin:distribution_channel_feeds_news_step"),
        {"step": "search_console", "done": "1"},
    )
    assert step.status_code == 302
    assert news.checklist()[1]["done"] is True


def test_the_picker_links_to_the_feeds_page(admin_client):
    body = admin_client.get(
        reverse("admin:distribution_channel_platforms")
    ).content.decode()
    assert reverse("admin:distribution_channel_feeds") in body
