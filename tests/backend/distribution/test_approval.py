"""Approval modes: pending -> approve (edit) / reject, reschedule, cancel; staff pages."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.distribution import services
from apps.distribution.models import ShareJob, ShareLog

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


@pytest.fixture
def pending(make_channel, make_content):
    channel = make_channel(
        "mastodon", use_composer=True, approval_mode="approval", timezone="UTC"
    )
    (job,) = services.enqueue(make_content(), now=NOW)
    return channel, job


def test_approval_channels_hold_posts_until_staff_decide(pending, net):
    _channel, job = pending
    assert job.status == ShareJob.Status.PENDING
    fetcher = net.Fetcher()
    later = NOW + timedelta(days=1)
    assert services.process_due_jobs(now=later, fetcher=fetcher) == services.Summary()
    assert fetcher.calls == []


def test_approving_queues_the_post_and_records_who_and_when(
    pending, django_user_model, net
):
    _channel, job = pending
    reviewer = django_user_model.objects.create_user(
        username="rev", email="rev@example.com"
    )
    assert services.approve_job(job, reviewer, now=NOW) is True
    job.refresh_from_db()
    assert job.status == ShareJob.Status.QUEUED
    assert job.decided_by == reviewer and job.decided_at == NOW
    assert ShareLog.objects.filter(
        job=job, message__startswith="Approved by staff"
    ).exists()
    assert services.approve_job(job, reviewer, now=NOW) is False  # already decided
    fetcher = net.Fetcher(net.ok({"id": "1"}))
    assert (
        services.process_due_jobs(now=NOW + timedelta(minutes=5), fetcher=fetcher).sent
        == 1
    )


def test_staff_can_edit_the_text_before_it_is_sent(pending, django_user_model, net):
    _channel, job = pending
    reviewer = django_user_model.objects.create_user(
        username="rev2", email="rev2@example.com"
    )
    edited = f"Hand written: new Acme firmware {job.url}"
    assert services.approve_job(job, reviewer, text=edited, now=NOW)
    job.refresh_from_db()
    assert job.text == edited
    assert ShareLog.objects.filter(job=job, message__startswith="Text edited").exists()
    fetcher = net.Fetcher(net.ok({"id": "1"}))
    services.process_due_jobs(now=NOW + timedelta(minutes=5), fetcher=fetcher)
    assert fetcher.bodies()[0]["status"] == edited


def test_an_edit_longer_than_the_platform_allows_is_refused(pending):
    _channel, job = pending
    with pytest.raises(ValueError, match="500"):
        services.approve_job(job, None, text="x" * 600, now=NOW)
    job.refresh_from_db()
    assert job.status == ShareJob.Status.PENDING


def test_rejecting_keeps_the_reason_and_never_sends(pending, django_user_model, net):
    _channel, job = pending
    reviewer = django_user_model.objects.create_user(
        username="rev3", email="rev3@example.com"
    )
    assert services.reject_job(job, reviewer, "Not newsworthy") is True
    job.refresh_from_db()
    assert (
        job.status == ShareJob.Status.REJECTED and job.decision_note == "Not newsworthy"
    )
    assert services.approve_job(job, reviewer) is False
    assert services.reject_job(job, reviewer) is False
    fetcher = net.Fetcher()
    services.process_due_jobs(now=NOW + timedelta(days=2), fetcher=fetcher)
    assert fetcher.calls == []


def test_approval_respects_the_channels_windows(make_channel, make_content):
    make_channel(
        "mastodon",
        use_composer=True,
        approval_mode="approval",
        timezone="UTC",
        send_windows="09:00-11:00",
    )
    (job,) = services.enqueue(make_content(), now=NOW)  # noon: window closed
    approved_at = datetime(2026, 11, 7, 18, 0, tzinfo=UTC)
    services.approve_job(job, None, now=approved_at)
    job.refresh_from_db()
    assert job.next_attempt_at == datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def test_staff_can_reschedule_or_cancel_waiting_posts(make_channel, make_content):
    make_channel("webhook")
    (job,) = services.enqueue(make_content(), now=NOW)
    target = NOW + timedelta(days=3)
    assert services.reschedule_job(job, target) is True
    job.refresh_from_db()
    assert job.next_attempt_at == target
    assert services.cancel_job(job) is True
    assert services.reschedule_job(job, NOW) is False
    assert services.cancel_job(job) is False


# ------------------------------------------------------------------ pages ----


def test_review_page_lists_pending_posts_with_editable_text(admin_client, pending):
    _channel, job = pending
    page = admin_client.get(reverse("admin:distribution_channel_review"))
    body = page.content.decode()
    assert page.status_code == 200
    assert job.title in body and "Approve" in body and "Reject" in body
    assert "<textarea" in body and 'name="decision"' in body


def test_approve_and_reject_through_the_review_page(
    admin_client, make_channel, make_content
):
    make_channel("mastodon", use_composer=True, approval_mode="approval")
    (first,) = services.enqueue(make_content(ref="official:1"))
    (second,) = services.enqueue(make_content(ref="official:2"))
    ok_url = reverse("admin:distribution_channel_job_decide", args=[first.pk])
    response = admin_client.post(
        ok_url, {"decision": "approve", "text": f"Edited by staff {first.url}"}
    )
    assert response.status_code == 302
    no_url = reverse("admin:distribution_channel_job_decide", args=[second.pk])
    admin_client.post(no_url, {"decision": "reject", "note": "duplicate"})
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.status == "queued" and first.text.startswith("Edited by staff")
    assert second.status == "rejected" and second.decision_note == "duplicate"
    assert admin_client.post(ok_url, {"decision": "bogus"}).status_code == 404


def test_an_overlong_edit_shows_an_error_and_changes_nothing(admin_client, pending):
    _channel, job = pending
    url = reverse("admin:distribution_channel_job_decide", args=[job.pk])
    response = admin_client.post(
        url, {"decision": "approve", "text": "y" * 900}, follow=True
    )
    assert "500 characters" in response.content.decode()
    job.refresh_from_db()
    assert job.status == "pending"


def test_calendar_groups_posts_by_day_and_allows_reschedule_and_cancel(
    admin_client, make_channel, make_content
):
    make_channel("webhook")
    (soon,) = services.enqueue(make_content(ref="official:1"))
    (gone,) = services.enqueue(make_content(ref="official:2"))
    page = admin_client.get(reverse("admin:distribution_channel_calendar"))
    body = page.content.decode()
    assert page.status_code == 200 and soon.title in body
    assert "Reschedule" in body and 'type="datetime-local"' in body
    target = timezone.localtime(timezone.now() + timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    resched = reverse("admin:distribution_channel_job_reschedule", args=[soon.pk])
    assert admin_client.post(resched, {"when": target}).status_code == 302
    soon.refresh_from_db()
    assert soon.next_attempt_at > timezone.now() + timedelta(days=1)
    bad = admin_client.post(resched, {"when": "garbage"}, follow=True)
    assert "valid date" in bad.content.decode()
    cancel = reverse("admin:distribution_channel_job_cancel", args=[gone.pk])
    response = admin_client.post(cancel, {"next": "https://evil.example/"})
    assert response.status_code == 302
    assert response["Location"] == reverse("admin:distribution_channel_calendar")
    gone.refresh_from_db()
    assert gone.status == "cancelled"


def test_calendar_is_empty_friendly(admin_client):
    page = admin_client.get(reverse("admin:distribution_channel_calendar"))
    assert "Nothing scheduled" in page.content.decode()


def test_job_actions_need_the_change_permission_and_csrf(
    staff_client, admin_client, pending, make_content
):
    _channel, job = pending
    decide = reverse("admin:distribution_channel_job_decide", args=[job.pk])
    viewer = staff_client("view_sharejob")
    assert viewer.get(reverse("admin:distribution_channel_calendar")).status_code == 200
    assert viewer.get(reverse("admin:distribution_channel_review")).status_code == 403
    assert viewer.post(decide, {"decision": "approve"}).status_code == 403
    reviewer = staff_client("change_sharejob", "view_sharejob")
    assert reviewer.get(reverse("admin:distribution_channel_review")).status_code == 200
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(_superuser())
    assert strict.post(decide, {"decision": "approve"}).status_code == 403
    job.refresh_from_db()
    assert job.status == "pending"


def _superuser():
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(username="d-admin")
