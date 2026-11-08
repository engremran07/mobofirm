"""Scheduling: windows, quiet hours, time zones, spacing, caps, schedule-only mode."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest

from apps.distribution import scheduling, services
from apps.distribution.models import Channel, ShareJob

UTC_NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)  # a Saturday
ISTANBUL = "Europe/Istanbul"  # UTC+3, no daylight saving


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=UTC)


def test_windows_parse_and_reject_bad_input():
    assert scheduling.parse_windows("09:00-11:00, 18:30-21:00") == [
        (time(9, 0), time(11, 0)),
        (time(18, 30), time(21, 0)),
    ]
    assert scheduling.parse_windows("") == []
    for bad in ("9", "25:00-26:00", "10:00-09:00", "ab-cd", "10:00-10:00"):
        with pytest.raises(ValueError):
            scheduling.parse_windows(bad)


def test_quiet_hours_may_wrap_midnight():
    assert scheduling.in_quiet(time(23, 0), time(22, 0), time(7, 0))
    assert scheduling.in_quiet(time(3, 0), time(22, 0), time(7, 0))
    assert not scheduling.in_quiet(time(12, 0), time(22, 0), time(7, 0))
    assert not scheduling.in_quiet(time(12, 0), None, None)


def test_next_allowed_waits_for_the_window_in_the_channel_time_zone():
    windows = scheduling.parse_windows("09:00-11:00")
    inside = at(7, 7, 0)  # 10:00 local
    assert scheduling.next_allowed(inside, tz_name=ISTANBUL, windows=windows) == inside
    early = scheduling.next_allowed(at(7, 5, 0), tz_name=ISTANBUL, windows=windows)
    assert early == at(7, 6, 0)  # 09:00 local
    late = scheduling.next_allowed(at(7, 8, 30), tz_name=ISTANBUL, windows=windows)
    assert late == at(8, 6, 0)  # tomorrow 09:00 local


def test_next_allowed_honours_quiet_hours_and_windows_together():
    windows = scheduling.parse_windows("06:00-09:00")

    def allowed_from(moment: datetime) -> datetime:
        return scheduling.next_allowed(
            moment,
            tz_name="UTC",
            windows=windows,
            quiet_start=time(7, 0),
            quiet_end=time(8, 0),
        )

    assert allowed_from(at(7, 5, 30)) == at(7, 6, 0)
    assert allowed_from(at(7, 7, 30)) == at(7, 8, 0)
    overnight = scheduling.next_allowed(
        at(7, 23, 0),
        tz_name="UTC",
        windows=[],
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
    )
    assert overnight == at(8, 7, 0)


def test_unknown_time_zone_falls_back_to_utc():
    assert not scheduling.valid_timezone("Mars/Base")
    windows = scheduling.parse_windows("09:00-11:00")
    result = scheduling.next_allowed(at(7, 5), tz_name="Mars/Base", windows=windows)
    assert result == at(7, 9, 0)


def test_local_day_bounds_follow_the_time_zone():
    start, end = scheduling.local_day_bounds(at(7, 22, 30), ISTANBUL)
    assert start == at(7, 21, 0) and end == at(8, 21, 0)


# ------------------------------------------------------------- planning ----


@pytest.mark.django_db
def test_jobs_are_spaced_by_the_platform_minimum_gap(make_channel, make_content):
    make_channel("telegram")  # 20 s minimum gap
    first, second, third = (
        services.enqueue(make_content(ref=f"official:{n}"), now=UTC_NOW)[0]
        for n in range(3)
    )
    assert first.next_attempt_at == UTC_NOW
    assert second.next_attempt_at == UTC_NOW + timedelta(seconds=20)
    assert third.next_attempt_at == UTC_NOW + timedelta(seconds=40)


@pytest.mark.django_db
def test_a_trending_launch_skips_the_backlog_but_not_the_windows(
    make_channel, make_content
):
    channel = make_channel("telegram", send_windows="09:00-23:00", timezone="UTC")
    for n in range(3):
        services.enqueue(make_content(ref=f"official:{n}"), now=UTC_NOW)
    (launch,) = services.enqueue(
        make_content(ref="official:new", trending=True), now=UTC_NOW
    )
    assert launch.next_attempt_at == UTC_NOW
    assert launch.trending is True
    night = datetime(2026, 11, 7, 2, 0, tzinfo=UTC)
    channel.refresh_from_db()
    (later,) = services.enqueue(
        make_content(ref="official:night", trending=True), now=night
    )
    assert later.next_attempt_at == at(7, 9, 0)


@pytest.mark.django_db
def test_schedule_only_channels_use_the_platform_default_windows(
    make_channel, make_content
):
    make_channel("webhook", approval_mode="schedule_only", timezone="UTC")
    early = datetime(2026, 11, 7, 3, 0, tzinfo=UTC)
    (job,) = services.enqueue(make_content(), now=early)
    assert job.status == ShareJob.Status.QUEUED
    assert job.next_attempt_at == at(7, 9, 0)


@pytest.mark.django_db
def test_quiet_hours_push_automatic_posts_to_the_morning(make_channel, make_content):
    make_channel(
        "webhook", quiet_start=time(22, 0), quiet_end=time(7, 0), timezone="UTC"
    )
    night = datetime(2026, 11, 7, 23, 0, tzinfo=UTC)
    (job,) = services.enqueue(make_content(), now=night)
    assert job.next_attempt_at == at(8, 7, 0)


@pytest.mark.django_db
def test_per_kind_daily_cap_moves_the_rest_to_the_next_day(make_channel, make_content):
    make_channel("webhook", kind_daily_cap=1, timezone="UTC")
    first = services.enqueue(make_content(ref="official:1"), now=UTC_NOW)[0]
    second = services.enqueue(make_content(ref="official:2"), now=UTC_NOW)[0]
    blog = services.enqueue(make_content("post:1", kind="blog"), now=UTC_NOW)[0]
    assert first.next_attempt_at == UTC_NOW
    assert second.next_attempt_at == at(8, 0, 0)
    assert blog.next_attempt_at == UTC_NOW  # another content type has its own cap


@pytest.mark.django_db
def test_a_job_waits_for_the_platform_gap_after_the_last_post(
    make_channel, make_content, net
):
    channel = make_channel("telegram")
    services.enqueue(make_content(), now=UTC_NOW)
    Channel.objects.filter(pk=channel.pk).update(
        last_success_at=UTC_NOW - timedelta(seconds=5)
    )
    fetcher = net.Fetcher()
    summary = services.process_due_jobs(now=UTC_NOW, fetcher=fetcher)
    assert summary.deferred == 1 and fetcher.calls == []
    job = ShareJob.objects.get()
    assert job.attempts == 0
    assert job.next_attempt_at == UTC_NOW + timedelta(seconds=15)
    later = UTC_NOW + timedelta(seconds=16)
    assert services.process_due_jobs(now=later, fetcher=fetcher).sent == 1
