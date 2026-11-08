"""Short links: tracked URLs, redirect, bot filtering, open-redirect safety, privacy."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.utils import timezone

from apps.core.app_service import AppService
from apps.core.models import AppRegistry
from apps.distribution import services, shortlinks
from apps.distribution.models import Campaign, ClickEvent, ShareJob, ShortLink

pytestmark = pytest.mark.django_db
HUMAN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


@pytest.fixture
def link(make_channel, make_content):
    make_channel("mastodon", use_composer=True)
    (job,) = services.enqueue(make_content())
    return ShortLink.objects.get(job=job)


def test_composed_shares_post_a_first_party_link_with_utm(link):
    job = link.job
    assert job.url == f"https://mobofirm.example/go/{link.code}/"
    assert job.url in job.text
    target = urlsplit(link.target_url)
    query = parse_qs(target.query)
    assert target.netloc == "mobofirm.example"
    assert query["utm_source"] == ["mastodon"] and query["utm_medium"] == ["social"]
    assert query["utm_campaign"] == ["firmware"] and query["utm_content"] == [job.hook]
    assert job.target_url == link.target_url


def test_a_preset_controls_the_utm_campaign_and_can_disable_short_links(
    make_channel, make_content
):
    preset = Campaign.objects.create(
        name="Launch", utm_campaign="spring", use_short_links=False
    )
    make_channel("mastodon", use_composer=True, campaign=preset)
    (job,) = services.enqueue(make_content())
    assert not ShortLink.objects.exists()
    assert "utm_campaign=spring" in job.url and "/go/" not in job.url


def test_legacy_template_channels_get_no_tracking_changes(make_channel, make_content):
    make_channel("webhook")
    (job,) = services.enqueue(make_content())
    assert not ShortLink.objects.exists()
    assert "utm_" not in job.url and job.target_url == ""


def test_tracked_target_keeps_the_existing_query_and_adds_the_referral():
    url = shortlinks.tracked_target(
        "https://mobofirm.example/page/?a=1",
        platform="x",
        campaign="",
        hook="question",
        referral="ABCD2345",
    )
    query = parse_qs(urlsplit(url).query)
    assert query["a"] == ["1"] and query["ref"] == ["ABCD2345"]
    assert query["utm_campaign"] == ["share"]


def test_the_redirect_goes_to_the_page_and_counts_a_human_click(link):
    response = Client().get(f"/go/{link.code}/", HTTP_USER_AGENT=HUMAN)
    assert response.status_code == 302 and response["Location"] == link.target_url
    assert response["Cache-Control"] == "no-store"
    assert "noindex" in response["X-Robots-Tag"]
    link.refresh_from_db()
    assert (link.clicks, link.bot_clicks) == (1, 0) and link.last_click_at is not None
    (event,) = ClickEvent.objects.all()
    assert event.bucket.minute == 0 and event.bucket.second == 0


def test_a_click_stores_no_personal_data():
    stored = {field.name for field in ClickEvent._meta.get_fields()}
    assert stored == {"id", "link", "bucket"}
    link_fields = {field.name for field in ShortLink._meta.get_fields()}
    assert (
        not {"ip", "ip_address", "user_agent", "referrer", "user", "cookie"}
        & link_fields
    )


@pytest.mark.parametrize(
    "agent",
    [
        "",
        "Googlebot/2.1 (+http://www.google.com/bot.html)",
        "facebookexternalhit/1.1",
        "Twitterbot/1.0",
        "Mozilla/5.0 (compatible; Discordbot/2.0)",
        "TelegramBot (like TwitterBot)",
        "WhatsApp/2.23",
        "Slackbot-LinkExpanding 1.0",
        "LinkedInBot/1.0",
        "python-requests/2.31",
        "curl/8.4.0",
        "Mozilla/5.0 HeadlessChrome/126.0",
    ],
)
def test_bots_and_link_previews_are_not_counted_as_clicks(agent, link):
    response = Client().get(f"/go/{link.code}/", HTTP_USER_AGENT=agent)
    assert response.status_code == 302
    link.refresh_from_db()
    assert (link.clicks, link.bot_clicks) == (0, 1)
    assert not ClickEvent.objects.exists()


def test_head_requests_and_prefetches_are_not_clicks(link):
    client = Client()
    client.head(f"/go/{link.code}/", HTTP_USER_AGENT=HUMAN)
    client.get(f"/go/{link.code}/", HTTP_USER_AGENT=HUMAN, HTTP_SEC_PURPOSE="prefetch")
    link.refresh_from_db()
    assert (link.clicks, link.bot_clicks) == (0, 2)


def test_only_safe_methods_are_allowed(link):
    assert Client().post(f"/go/{link.code}/").status_code == 405


def test_unknown_codes_and_a_disabled_app_are_404(link):
    assert Client().get("/go/nosuchcode/", HTTP_USER_AGENT=HUMAN).status_code == 404
    registry = AppRegistry.get_solo()
    registry.distribution_enabled = False
    registry.save()
    assert Client().get(f"/go/{link.code}/", HTTP_USER_AGENT=HUMAN).status_code == 404


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/phish",
        "//evil.example/phish",
        "javascript:alert(1)",
        "https://mobofirm.example.evil.example/",
        "ftp://mobofirm.example/file",
        "/relative/path",
    ],
)
def test_the_redirect_cannot_leave_the_site(target, link):
    """Even a tampered row cannot turn /go/ into an open redirect."""
    ShortLink.objects.filter(pk=link.pk).update(target_url=target)
    response = Client().get(f"/go/{link.code}/", HTTP_USER_AGENT=HUMAN)
    assert response.status_code == 404
    link.refresh_from_db()
    assert link.clicks == 0


def test_the_destination_is_never_taken_from_the_request(link):
    response = Client().get(
        f"/go/{link.code}/?next=https://evil.example/&url=https://evil.example/",
        HTTP_USER_AGENT=HUMAN,
    )
    assert response["Location"] == link.target_url


def test_referral_codes_come_through_the_referral_contract(
    make_channel, make_content, django_user_model, monkeypatch
):
    sharer = django_user_model.objects.create_user(
        username="sharer", email="s@example.com"
    )
    preset = Campaign.objects.create(name="Referral", use_referral=True)
    make_channel("mastodon", use_composer=True, campaign=preset, referral_user=sharer)
    gateway = SimpleNamespace(referral_code_for=lambda user: "SHARE234")
    monkeypatch.setattr(AppService, "get", classmethod(lambda cls, label: gateway))
    (job,) = services.enqueue(make_content())
    assert parse_qs(urlsplit(job.target_url).query)["ref"] == ["SHARE234"]


def test_without_the_referral_contract_no_ref_parameter_is_added(
    make_channel, make_content, django_user_model, monkeypatch
):
    sharer = django_user_model.objects.create_user(
        username="sharer2", email="s2@example.com"
    )
    preset = Campaign.objects.create(name="Referral", use_referral=True)
    make_channel("mastodon", use_composer=True, campaign=preset, referral_user=sharer)
    monkeypatch.setattr(AppService, "get", classmethod(lambda cls, label: None))
    (job,) = services.enqueue(make_content())
    assert "ref" not in parse_qs(urlsplit(job.target_url).query)


def test_referral_is_off_unless_the_preset_allows_it(
    make_channel, make_content, django_user_model, monkeypatch
):
    sharer = django_user_model.objects.create_user(
        username="sharer3", email="s3@example.com"
    )
    make_channel("mastodon", use_composer=True, referral_user=sharer)
    gateway = SimpleNamespace(referral_code_for=lambda user: "SHARE234")
    monkeypatch.setattr(AppService, "get", classmethod(lambda cls, label: gateway))
    (job,) = services.enqueue(make_content())
    assert "ref" not in parse_qs(urlsplit(job.target_url).query)


def test_the_real_referral_contract_links_shares_to_the_sharers_code(
    make_channel, make_content, django_user_model
):
    from apps.referral import services as referral_services

    sharer = django_user_model.objects.create_user(
        username="real", email="real@example.com"
    )
    preset = Campaign.objects.create(name="Referral", use_referral=True)
    make_channel("mastodon", use_composer=True, campaign=preset, referral_user=sharer)
    (job,) = services.enqueue(make_content())
    code = referral_services.get_or_create_code(sharer).code
    assert parse_qs(urlsplit(job.target_url).query)["ref"] == [code]
    settings = referral_services.get_settings()
    settings.enabled = False
    settings.save()
    assert shortlinks.referral_code(sharer) == ""
    assert shortlinks.referral_code(None) == ""


def test_deleting_a_job_never_breaks_old_links(link):
    assert ShareJob.objects.filter(pk=link.job_id).exists()
    assert shortlinks.short_url(link.code).endswith(f"/go/{link.code}/")
    now = timezone.now()
    shortlinks.record_click(link, bot=False, now=now)
    assert ShortLink.objects.get(pk=link.pk).clicks == 1
