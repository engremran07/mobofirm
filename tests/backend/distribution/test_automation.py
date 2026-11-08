"""Growth automation: A/B winner selection, evergreen re-shares, retention housekeeping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command

from apps.distribution import analytics, automation, content, services, tasks, variants
from apps.distribution.models import (
    Campaign,
    ClickEvent,
    DistributionSettings,
    MediaAsset,
    ShareJob,
    ShareLog,
    ShortLink,
)

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def sent_job(
    channel, ref, *, hook="question", clicks=0, days_ago=40, round_=0, status="sent"
):
    job = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref=ref,
        idempotency_key=f"{channel.pk}:firmware:{ref}:{round_}:{hook}",
        title=f"Title {ref}",
        text="text",
        hook=hook,
        reshare_round=round_,
        status=status,
        sent_at=NOW - timedelta(days=days_ago),
    )
    ShareJob.objects.filter(pk=job.pk).update(created_at=NOW - timedelta(days=days_ago))
    ShortLink.objects.create(
        code=f"c{job.pk:06d}",
        job=job,
        channel=channel,
        content_kind="firmware",
        object_ref=ref,
        hook=hook,
        target_url="https://mobofirm.example/x/",
        clicks=clicks,
    )
    ShortLink.objects.filter(job=job).update(created_at=NOW - timedelta(days=days_ago))
    return job


# ------------------------------------------------------------------ A/B ----


def test_styles_rotate_least_used_first_until_enough_clicks(make_channel):
    preset = Campaign.objects.create(
        name="ab", hook_styles="question,benefit", ab_min_clicks=50
    )
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    assert variants.choose_hook(channel, preset, now=NOW) == ("question", "A")
    sent_job(channel, "official:1", hook="question", days_ago=1, clicks=3)
    assert variants.choose_hook(channel, preset, now=NOW) == ("benefit", "B")
    sent_job(channel, "official:2", hook="benefit", days_ago=1, clicks=1)
    assert variants.choose_hook(channel, preset, now=NOW) == ("question", "A")


def test_the_winner_is_chosen_after_enough_clicks_and_then_always_used(make_channel):
    preset = Campaign.objects.create(
        name="ab2", hook_styles="question,benefit,howto", ab_min_clicks=20
    )
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    for index, (hook, clicks) in enumerate(
        [
            ("question", 4),
            ("question", 4),
            ("benefit", 20),
            ("benefit", 14),
            ("howto", 1),
            ("howto", 0),
        ]
    ):
        sent_job(channel, f"official:{index}", hook=hook, clicks=clicks, days_ago=2)
    result = variants.evaluate(channel, preset, NOW)
    assert result.total_clicks == 43 and result.decided and result.winner == "benefit"
    assert result.stats[1].per_post == 17
    for _ in range(3):
        assert variants.choose_hook(channel, preset, now=NOW)[0] == "benefit"


def test_no_winner_before_every_style_was_tried_twice(make_channel):
    preset = Campaign.objects.create(
        name="ab3", hook_styles="question,benefit", ab_min_clicks=5
    )
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    sent_job(channel, "official:1", hook="question", clicks=50, days_ago=1)
    sent_job(channel, "official:2", hook="question", clicks=50, days_ago=1)
    sent_job(channel, "official:3", hook="benefit", clicks=0, days_ago=1)
    assert not variants.evaluate(channel, preset, NOW).decided


def test_without_ab_testing_the_first_style_is_used(make_channel):
    preset = Campaign.objects.create(
        name="plain", hook_styles="benefit,question", ab_test=False
    )
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    sent_job(channel, "official:1", hook="benefit", days_ago=1)
    assert variants.choose_hook(channel, preset, now=NOW)[0] == "benefit"


def test_cancelled_and_failed_posts_do_not_count_as_trials(make_channel):
    preset = Campaign.objects.create(name="ab4", hook_styles="question,benefit")
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    sent_job(channel, "official:1", hook="question", days_ago=1, status="cancelled")
    sent_job(channel, "official:2", hook="question", days_ago=1, status="failed")
    assert variants.choose_hook(channel, preset, now=NOW)[0] == "question"


def test_analytics_report_the_ab_standing(make_channel):
    preset = Campaign.objects.create(name="ab5", hook_styles="question,benefit")
    channel = make_channel("mastodon", use_composer=True, campaign=preset)
    sent_job(channel, "official:1", hook="question", days_ago=1, clicks=2)
    (row,) = analytics.per_variant(NOW)
    assert row["channel"] == channel and row["result"].stats[0].clicks == 2


# ------------------------------------------------------------ evergreen ----


@pytest.fixture
def evergreen(make_channel, make_content, monkeypatch):
    preset = Campaign.objects.create(
        name="evergreen", evergreen=True, evergreen_gap_days=30, evergreen_per_week=2
    )
    channel = make_channel(
        "webhook", use_composer=True, campaign=preset, timezone="UTC"
    )
    monkeypatch.setattr(
        content,
        "load",
        lambda kind, ref: make_content(ref=ref, title=f"Title {ref}", kind=kind),
    )
    return channel, preset


def test_top_performers_are_reshared_with_an_honest_evergreen_hook(evergreen):
    channel, _preset = evergreen
    sent_job(channel, "official:best", clicks=9)
    created = automation.run_evergreen(NOW)
    assert created == 1
    job = ShareJob.objects.get(reshare_round=1)
    assert job.idempotency_key.endswith(":official:best:r1")
    assert job.hook == "evergreen" and "Still useful" in job.text
    assert "Just added" not in job.text


def test_a_reshare_is_never_duplicated_and_respects_the_gap(evergreen):
    channel, _preset = evergreen
    sent_job(channel, "official:best", clicks=9)
    sent_job(channel, "official:recent", clicks=9, days_ago=10)
    assert automation.run_evergreen(NOW) == 1
    assert automation.run_evergreen(NOW) == 0
    assert automation.run_evergreen(NOW + timedelta(days=1)) == 0
    refs = set(
        ShareJob.objects.filter(reshare_round__gt=0).values_list(
            "object_ref", flat=True
        )
    )
    assert refs == {"official:best"}


def test_the_weekly_cap_limits_reshares_and_best_content_goes_first(evergreen):
    channel, preset = evergreen
    preset.evergreen_per_week = 1
    preset.save()
    sent_job(channel, "official:ok", clicks=5)
    sent_job(channel, "official:top", clicks=50)
    assert automation.run_evergreen(NOW) == 1
    assert ShareJob.objects.get(reshare_round=1).object_ref == "official:top"


def test_weak_unpublished_or_unshared_content_is_not_reshared(
    make_channel, make_content, monkeypatch
):
    preset = Campaign.objects.create(name="eg2", evergreen=True)
    channel = make_channel("webhook", use_composer=True, campaign=preset)
    sent_job(channel, "official:weak", clicks=1)
    sent_job(channel, "official:gone", clicks=40)
    monkeypatch.setattr(
        content,
        "load",
        lambda kind, ref: None if ref.endswith("gone") else make_content(ref=ref),
    )
    assert automation.run_evergreen(NOW) == 0
    other = make_channel("slack", use_composer=True, campaign=preset, name="other")
    sent_job(other, "official:elsewhere", clicks=30)
    monkeypatch.setattr(content, "load", lambda kind, ref: make_content(ref=ref))
    ShareJob.objects.filter(channel=other).delete()
    # elsewhere's links are gone with its jobs; the webhook channel never shared that item
    assert automation.run_evergreen(NOW) == 0


def test_channels_without_the_evergreen_preset_are_left_alone(
    make_channel, make_content, monkeypatch
):
    preset = Campaign.objects.create(name="eg3", evergreen=False)
    channel = make_channel("webhook", use_composer=True, campaign=preset)
    monkeypatch.setattr(content, "load", lambda kind, ref: make_content(ref=ref))
    sent_job(channel, "official:best", clicks=9)
    assert automation.run_evergreen(NOW) == 0


def test_reshares_go_through_the_normal_pipeline(evergreen, net):
    channel, _preset = evergreen
    sent_job(channel, "official:best", clicks=9)
    automation.run_evergreen(NOW)
    fetcher = net.Fetcher(net.ok())
    assert (
        services.process_due_jobs(now=NOW + timedelta(minutes=1), fetcher=fetcher).sent
        == 1
    )


# ---------------------------------------------------------- housekeeping ----


def test_retention_purges_old_clicks_logs_and_media_but_keeps_totals(make_channel):
    channel = make_channel("webhook")
    job = sent_job(channel, "official:1", clicks=7, days_ago=1)
    link = ShortLink.objects.get(job=job)
    ClickEvent.objects.create(link=link, bucket=NOW - timedelta(days=200))
    ClickEvent.objects.create(link=link, bucket=NOW - timedelta(days=3))
    old_log = ShareLog.objects.create(channel=channel, message="old")
    ShareLog.objects.filter(pk=old_log.pk).update(created_at=NOW - timedelta(days=400))
    ShareLog.objects.create(channel=channel, message="new")
    name = default_storage.save("distribution/card/old.png", ContentFile(b"png"))
    asset = MediaAsset.objects.create(
        content_kind="firmware",
        object_ref="official:1",
        asset_kind="card",
        storage_name=name,
    )
    MediaAsset.objects.filter(pk=asset.pk).update(created_at=NOW - timedelta(days=90))
    result = automation.housekeeping(NOW, force=True)
    assert result["clicks"] == 1 and result["logs"] == 1 and result["media"] == 1
    assert ClickEvent.objects.count() == 1
    assert ShareLog.objects.filter(message="old").count() == 0
    assert ShareLog.objects.filter(message="new").count() == 1
    assert not default_storage.exists(name) and not MediaAsset.objects.exists()
    link.refresh_from_db()
    assert link.clicks == 7


def test_zero_retention_keeps_everything(make_channel):
    DistributionSettings.objects.update_or_create(
        pk=1,
        defaults={
            "click_retention_days": 0,
            "log_retention_days": 0,
            "media_retention_days": 0,
        },
    )
    link = ShortLink.objects.get(job=sent_job(make_channel("webhook"), "official:1"))
    ClickEvent.objects.create(link=link, bucket=NOW - timedelta(days=900))
    result = automation.housekeeping(NOW, force=True)
    assert result["clicks"] == 0 and result["logs"] == 0 and result["media"] == 0
    assert ClickEvent.objects.count() == 1


def test_housekeeping_runs_at_most_once_an_hour_unless_forced():
    assert automation.housekeeping(NOW) != {}
    assert automation.housekeeping(NOW + timedelta(minutes=10)) == {}
    assert automation.housekeeping(NOW + timedelta(hours=2)) != {}
    assert (
        automation.housekeeping(NOW + timedelta(hours=2, minutes=1), force=True) != {}
    )


def test_the_minutely_task_delivers_and_runs_housekeeping(make_channel, make_content):
    make_channel("webhook")
    summary = tasks.process_share_jobs()
    assert summary == {"sent": 0, "retried": 0, "failed": 0, "deferred": 0}
    assert DistributionSettings.get_solo().last_housekeeping_at is not None
    assert "evergreen" in tasks.run_housekeeping()


def test_the_management_command_reports_the_run():
    out = StringIO()
    call_command("distribution_jobs", "--housekeeping", stdout=out)
    assert "sent=0" in out.getvalue() and "housekeeping=" in out.getvalue()
