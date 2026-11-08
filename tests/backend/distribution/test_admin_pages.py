"""Admin: platform picker, per-platform credential forms, dashboard, analytics, CSV,
permissions, navigation."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.core import admin_navigation
from apps.distribution import channels, services
from apps.distribution.models import (
    Campaign,
    Channel,
    ClickEvent,
    ShareJob,
    ShortLink,
)

pytestmark = pytest.mark.django_db
BASE = {
    "name": "Telegram news",
    "kind": "telegram",
    "share_firmware": "on",
    "share_blog_posts": "on",
    "use_composer": "on",
    "firmware_template": "New firmware: {title}\n{url}",
    "blog_template": "New on {site}: {title}\n{url}",
    "approval_mode": "auto",
    "timezone": "UTC",
    "send_windows": "",
    "delay_minutes": 0,
    "daily_limit": 20,
    "kind_daily_cap": 0,
    "max_attempts": 5,
    "jobs-TOTAL_FORMS": 0,
    "jobs-INITIAL_FORMS": 0,
}


def add_url(kind):
    return f"{reverse('admin:distribution_channel_add')}?kind={kind}"


# ------------------------------------------------------- picker and forms ----


def test_adding_without_a_platform_opens_the_picker(admin_client):
    response = admin_client.get(reverse("admin:distribution_channel_add"))
    assert response.status_code == 200
    for page in (
        response,
        admin_client.get(reverse("admin:distribution_channel_platforms")),
    ):
        body = page.content.decode()
        assert page.status_code == 200 and "mf-ui" in body
        for adapter in channels.adapters().values():
            assert adapter.label in body
        assert "Needs video" in body and "Needs image" in body
        assert "reddit.com/prefs/apps" in body  # how-to-get-credentials hint
        assert "?kind=reddit" in body


@pytest.mark.parametrize("kind", sorted(channels.adapters()))
def test_each_platform_gets_a_form_with_its_own_fields_and_hint(kind, admin_client):
    adapter = channels.get_adapter(kind)
    assert adapter is not None
    page = admin_client.get(add_url(kind))
    body = page.content.decode()
    assert page.status_code == 200
    for spec in adapter.fields:
        assert f'name="cred_{spec.name}"' in body
        assert spec.label in body
    assert 'name="kind"' in body and f'value="{kind}"' in body
    assert "Approval" in body or "approval_mode" in body
    for other in channels.adapters().values():
        for spec in other.fields:
            if spec.name not in {f.name for f in adapter.fields}:
                assert f'name="cred_{spec.name}"' not in body


def test_credentials_are_write_only_and_stored_encrypted(admin_client):
    data = {
        **BASE,
        "cred_chat_id": "@mobofirm",
        "cred_bot_token": "999:SECRET-TOKEN",
        "enabled": "on",
    }
    response = admin_client.post(add_url("telegram"), data)
    assert response.status_code == 302, response.context["adminform"].form.errors
    channel = Channel.objects.get(name="Telegram news")
    assert "SECRET-TOKEN" not in channel.secret_encrypted
    assert channel.target == "@mobofirm" and channel.state == "ready"
    page = admin_client.get(
        reverse("admin:distribution_channel_change", args=[channel.pk])
    )
    body = page.content.decode()
    assert page.status_code == 200 and "SECRET-TOKEN" not in body
    assert "A credential is stored" in body
    assert 'value="@mobofirm"' in body  # plain settings are shown, secrets never


def test_editing_without_retyping_the_secret_keeps_it(admin_client, make_channel):
    channel = make_channel("telegram")
    data = {**BASE, "name": channel.name, "cred_chat_id": "@renamed", "enabled": "on"}
    url = reverse("admin:distribution_channel_change", args=[channel.pk])
    assert admin_client.post(url, data).status_code == 302
    channel.refresh_from_db()
    assert channel.target == "@renamed"
    assert services.stored_values(channel)["bot_token"] == "123:BOT-TOKEN"


def test_the_clear_option_removes_stored_credentials(admin_client, make_channel):
    channel = make_channel("telegram")
    data = {
        **BASE,
        "name": channel.name,
        "cred_chat_id": "@mobofirm",
        "clear_secret": "on",
    }
    url = reverse("admin:distribution_channel_change", args=[channel.pk])
    assert admin_client.post(url, data).status_code == 302
    channel.refresh_from_db()
    assert not channel.has_secret and channel.enabled is False


def test_admin_refuses_to_enable_an_incomplete_channel_and_bad_input(admin_client):
    bad = {
        **BASE,
        "kind": "discord",
        "enabled": "on",
        "firmware_template": "{title} {url} {nope}",
    }
    response = admin_client.post(add_url("discord"), bad)
    assert response.status_code == 200
    assert "Unknown placeholder" in response.content.decode()
    assert not Channel.objects.exists()
    incomplete = {**BASE, "kind": "discord", "enabled": "on"}
    response = admin_client.post(add_url("discord"), incomplete)
    assert response.status_code == 200 and "needs" in response.content.decode()
    assert not Channel.objects.exists()
    for field, value, message in (
        ("send_windows", "25:00-26:00", "valid time"),
        ("timezone", "Mars/Base", "Unknown time zone"),
    ):
        data = {**BASE, "kind": "discord", field: value}
        response = admin_client.post(add_url("discord"), data)
        assert response.status_code == 200 and message in response.content.decode()


def test_a_discord_channel_is_saved_off_with_partial_config(admin_client):
    data = {**BASE, "kind": "discord", "name": "Draft", "cred_webhook_url": ""}
    assert admin_client.post(add_url("discord"), data).status_code == 302
    channel = Channel.objects.get(name="Draft")
    assert channel.enabled is False and channel.state == "off"


def test_the_platform_cannot_be_changed_after_creation(admin_client, make_channel):
    channel = make_channel("telegram")
    page = admin_client.get(
        reverse("admin:distribution_channel_change", args=[channel.pk])
    )
    assert 'name="kind"' not in page.content.decode()
    data = {
        **BASE,
        "name": channel.name,
        "kind": "discord",
        "cred_chat_id": "@mobofirm",
    }
    admin_client.post(
        reverse("admin:distribution_channel_change", args=[channel.pk]), data
    )
    channel.refresh_from_db()
    assert channel.kind == "telegram"


def test_connection_test_and_test_post_actions_report_results(
    admin_client, make_channel, net, monkeypatch
):
    channel = make_channel("telegram")
    fetcher = net.Fetcher(
        net.ok({"ok": True, "result": {"username": "mobo_bot"}}),
        net.ok({"ok": True, "result": {"message_id": 5}}),
        net.reply(500),
    )
    monkeypatch.setattr(services, "default_fetcher", lambda: fetcher)
    check = reverse("admin:distribution_channel_check", args=[channel.pk])
    page = admin_client.post(check, follow=True)
    assert "Connected as @mobo_bot" in page.content.decode()
    test = reverse("admin:distribution_channel_test_post", args=[channel.pk])
    assert "delivered" in admin_client.post(test, follow=True).content.decode()
    assert "failed" in admin_client.post(test, follow=True).content.decode()
    channel.refresh_from_db()
    assert channel.connection_ok is True


def test_the_preview_page_is_a_dry_run(admin_client, make_channel, net, monkeypatch):
    channel = make_channel("mastodon", use_composer=True)
    fetcher = net.Fetcher()
    monkeypatch.setattr(services, "default_fetcher", lambda: fetcher)
    page = admin_client.get(
        reverse("admin:distribution_channel_preview", args=[channel.pk])
    )
    body = page.content.decode()
    assert page.status_code == 200 and "Dry run" in body and "Sample content" in body
    assert "/ 500 characters" in body and "api/v1/statuses" in body
    assert fetcher.calls == [] and not ShareJob.objects.exists()


# ------------------------------------------------------------ dashboard ----


def test_dashboard_shows_health_queue_clicks_and_actions(
    admin_client, make_channel, make_content, net
):
    healthy = make_channel("webhook", name="Hooks")
    broken = make_channel("telegram", name="Telegram", consecutive_failures=4)
    Channel.objects.filter(pk=broken.pk).update(
        consecutive_failures=4, last_error="HTTP 403"
    )
    services.enqueue(make_content())
    services.process_due_jobs(fetcher=net.Fetcher(net.ok(), net.reply(404)))
    first_job = ShareJob.objects.first()
    assert first_job is not None
    link = ShortLink.objects.create(
        code="aaaaaaa",
        job=first_job,
        channel=healthy,
        content_kind="firmware",
        object_ref="official:abc",
        target_url="https://mobofirm.example/x/",
    )
    ClickEvent.objects.create(link=link, bucket=timezone.now())
    page = admin_client.get(reverse("admin:distribution_channel_dashboard"))
    body = page.content.decode()
    assert page.status_code == 200 and 'class="mf-ui"' in body
    assert "Hooks" in body and "Telegram" in body and "Run queue now" in body
    assert "Test connection" in body and "Send test post" in body and "Preview" in body
    assert "Circuit breaker" in body and "5 / 10" in body
    assert (
        "Queue depth" in body and "Link clicks" in body and "Top shared content" in body
    )
    assert "HTTP 404" in body
    assert (
        "card shadow" not in body and "btn-primary" not in body
    )  # no Bootstrap classes
    assert page.context["clicks_7d"] == 1


def test_dashboard_tells_staff_when_media_tools_are_missing(admin_client, monkeypatch):
    from apps.distribution import media

    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    body = admin_client.get(
        reverse("admin:distribution_channel_dashboard")
    ).content.decode()
    assert "ffmpeg was not found" in body


def test_run_queue_delivers_due_posts_and_reports(
    admin_client, make_channel, make_content, net, monkeypatch
):
    make_channel("webhook")
    services.enqueue(make_content())
    monkeypatch.setattr(services, "default_fetcher", lambda: net.Fetcher(net.ok()))
    page = admin_client.post(
        reverse("admin:distribution_channel_run_queue"), follow=True
    )
    assert "1 sent" in page.content.decode()
    assert ShareJob.objects.get().status == "sent"
    assert (
        admin_client.get(reverse("admin:distribution_channel_run_queue")).status_code
        == 405
    )


def test_changelists_render(admin_client, make_channel, make_content):
    make_channel("webhook")
    services.enqueue(make_content())
    for name in (
        "channel",
        "campaign",
        "sharejob",
        "sharelog",
        "shortlink",
        "mediaasset",
    ):
        url = reverse(f"admin:distribution_{name}_changelist")
        assert admin_client.get(url).status_code == 200, name
    assert (
        admin_client.get(reverse("admin:distribution_sharejob_add")).status_code == 403
    )
    assert admin_client.get(
        reverse("admin:distribution_distributionsettings_changelist")
    ).status_code in {200, 302}


def test_campaign_presets_are_staff_editable(admin_client):
    data = {
        "name": "Launch week",
        "description": "",
        "hook_styles": "benefit,urgency",
        "emoji_policy": "minimal",
        "hashtag_cap": 0,
        "use_short_links": "on",
        "utm_campaign": "launch",
        "ab_test": "on",
        "ab_min_clicks": 40,
        "evergreen_gap_days": 45,
        "evergreen_per_week": 2,
        "trending_boost": "on",
        "generate_media": "on",
    }
    response = admin_client.post(reverse("admin:distribution_campaign_add"), data)
    assert response.status_code == 302
    assert Campaign.objects.get().hooks == ("benefit", "urgency")
    bad = admin_client.post(
        reverse("admin:distribution_campaign_add"),
        {**data, "name": "Bad", "hook_styles": "shout"},
    )
    assert bad.status_code == 200 and "Choose from" in bad.content.decode()


# ------------------------------------------------------------ analytics ----


def make_link(channel, **extra):
    job = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref="official:1",
        idempotency_key=f"k:{ShareJob.objects.count()}",
        title="Acme One",
        text="t",
        status="sent",
        sent_at=timezone.now(),
    )
    return ShortLink.objects.create(
        code=f"code{job.pk:03d}",
        job=job,
        channel=channel,
        content_kind="firmware",
        object_ref="official:1",
        hook="question",
        target_url="https://mobofirm.example/x/",
        **extra,
    )


def test_analytics_page_shows_clicks_variants_and_the_heatmap(
    admin_client, make_channel
):
    channel = make_channel("webhook", name="Hooks")
    link = make_link(channel, clicks=3)
    base = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)
    for offset in (0, 1):
        ClickEvent.objects.create(
            link=link,
            bucket=timezone.now().replace(minute=0, second=0, microsecond=0)
            - timedelta(hours=offset),
        )
    ClickEvent.objects.create(link=link, bucket=base)
    page = admin_client.get(
        reverse("admin:distribution_channel_analytics") + "?days=90"
    )
    body = page.content.decode()
    assert (
        page.status_code == 200 and "By channel" in body and "Best-hour heatmap" in body
    )
    assert (
        "Hooks" in body and "A/B opening styles" in body and "Clicks per post" in body
    )
    assert page.context["total"] == 3 and page.context["heatmap"]["peak"] >= 1
    assert page.context["heatmap"]["best"]
    assert (
        admin_client.get(
            reverse("admin:distribution_channel_analytics") + "?days=abc"
        ).status_code
        == 200
    )


def test_csv_export_streams_links_and_neutralises_formulas(admin_client, make_channel):
    channel = make_channel("webhook", name='=HYPERLINK("http://evil")')
    make_link(channel, clicks=4, bot_clicks=1)
    response = admin_client.get(reverse("admin:distribution_channel_analytics_export"))
    assert response.status_code == 200 and response["Content-Type"].startswith(
        "text/csv"
    )
    assert "no-store" in response["Cache-Control"]
    body = b"".join(response.streaming_content).decode()
    rows = list(csv.reader(io.StringIO(body)))
    assert rows[0][:3] == ["code", "channel", "platform"]
    assert rows[1][1].startswith("'=") and rows[1][7] == "4" and rows[1][8] == "1"


# ----------------------------------------------------------- permissions ----


def test_staff_pages_enforce_model_permissions(staff_client, make_channel):
    channel = make_channel("webhook")
    nobody = staff_client()
    for name in ("dashboard", "calendar", "review", "analytics", "platforms"):
        assert (
            nobody.get(reverse(f"admin:distribution_channel_{name}")).status_code == 403
        ), name
    viewer = staff_client("view_channel")
    assert (
        viewer.get(reverse("admin:distribution_channel_dashboard")).status_code == 200
    )
    assert (
        viewer.get(
            reverse("admin:distribution_channel_preview", args=[channel.pk])
        ).status_code
        == 200
    )
    assert (
        viewer.post(reverse("admin:distribution_channel_run_queue")).status_code == 403
    )
    assert (
        viewer.post(
            reverse("admin:distribution_channel_check", args=[channel.pk])
        ).status_code
        == 403
    )
    assert (
        viewer.post(
            reverse("admin:distribution_channel_test_post", args=[channel.pk])
        ).status_code
        == 403
    )
    assert (
        viewer.get(reverse("admin:distribution_channel_platforms")).status_code == 403
    )
    adder = staff_client("add_channel", "view_channel")
    assert adder.get(reverse("admin:distribution_channel_platforms")).status_code == 200
    analyst = staff_client("view_shortlink")
    assert (
        analyst.get(reverse("admin:distribution_channel_analytics")).status_code == 200
    )
    assert (
        analyst.get(reverse("admin:distribution_channel_analytics_export")).status_code
        == 200
    )
    assert (
        nobody.get(reverse("admin:distribution_channel_analytics_export")).status_code
        == 403
    )


def test_anonymous_visitors_are_sent_to_login(client):
    for name in ("dashboard", "calendar", "review", "analytics", "platforms"):
        response = client.get(reverse(f"admin:distribution_channel_{name}"))
        assert response.status_code == 302 and "login" in response["Location"], name


# ------------------------------------------------------------- navigation ----


def test_distribution_is_a_top_level_sidebar_group(admin_client):
    titles = [group.title for group in admin_navigation.NAV_GROUPS]
    assert "Distribution" in titles
    community = next(g for g in admin_navigation.NAV_GROUPS if g.title == "Community")
    leftovers = [
        entry
        for entry in community.entries
        if "distribution" in getattr(entry, "label", getattr(entry, "url_name", ""))
    ]
    assert leftovers == []
    request = RequestFactory().get("/admin/")
    request.user = get_user_model().objects.get(username="d-admin")
    sidebar = admin_navigation.build_sidebar(request)
    group = next(g for g in sidebar if g["title"] == "Distribution")
    names = [item["title"] for item in group["items"]]
    for expected in (
        "Dashboard",
        "Channels",
        "Campaign presets",
        "Content calendar",
        "Approval queue",
        "Share queue",
        "Link analytics",
        "Distribution log",
    ):
        assert expected in names, expected
    assert all(item["link"] for item in group["items"])
