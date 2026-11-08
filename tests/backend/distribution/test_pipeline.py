"""Pipeline: enqueue idempotency, delivery/retry semantics, circuit breaker, wiring."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.events.bus import event_bus
from apps.core.models import AppRegistry
from apps.distribution import channels, services
from apps.distribution.constants import CHANNEL_TRIP_AFTER, RETRY_BACKOFF
from apps.distribution.models import Channel, ShareJob, ShareLog, validate_template
from apps.seo.fetcher import BlockedDestination, FetchTimeout, SafeFetcher

pytestmark = pytest.mark.django_db
SITE = "https://mobofirm.example"


def test_new_channels_are_off_and_post_nothing(make_content):
    channel = Channel.objects.create(name="draft", kind="webhook")
    assert channel.enabled is False
    assert channel.state == "off"
    assert services.enqueue(make_content()) == []
    assert not ShareJob.objects.exists()


def test_enqueue_is_idempotent_per_channel_and_object(make_channel, make_content):
    first, second = make_channel("webhook"), make_channel("telegram")
    jobs = services.enqueue(make_content())
    assert {j.channel_id for j in jobs} == {first.pk, second.pk}
    assert services.enqueue(make_content()) == []
    assert ShareJob.objects.count() == 2
    assert len(services.enqueue(make_content(ref="official:other"))) == 2


def test_topics_completeness_and_flag_are_respected(make_channel, make_content, net):
    firmware_only = make_channel("webhook", share_blog_posts=False)
    blog_only = make_channel("telegram", share_firmware=False)
    incomplete = Channel.objects.create(name="no-url", kind="webhook", enabled=True)
    assert incomplete.state == "misconfigured"
    firmware_jobs = services.enqueue(make_content())
    assert [j.channel_id for j in firmware_jobs] == [firmware_only.pk]
    blog_jobs = services.enqueue(make_content("post:9", kind="blog"))
    assert [j.channel_id for j in blog_jobs] == [blog_only.pk]
    registry = AppRegistry.get_solo()
    registry.distribution_enabled = False
    registry.save()
    assert services.enqueue(make_content(ref="official:new")) == []
    assert services.process_due_jobs(fetcher=net.Fetcher()) == services.Summary()


def test_daily_limit_records_a_visible_skip(make_channel, make_content):
    channel = make_channel("webhook", daily_limit=1)
    assert len(services.enqueue(make_content())) == 1
    assert services.enqueue(make_content(ref="official:two")) == []
    skipped = ShareJob.objects.get(object_ref="official:two")
    assert skipped.status == ShareJob.Status.SKIPPED
    assert "Daily limit" in skipped.last_error
    assert ShareLog.objects.filter(channel=channel, level="warning").exists()


def test_the_platform_ceiling_caps_the_daily_limit(make_channel, make_content):
    channel = make_channel("reddit", daily_limit=50)
    assert services.effective_daily_limit(channel) == 5


def test_delay_postpones_the_first_attempt(make_channel, make_content, net):
    make_channel("webhook", delay_minutes=30)
    now = timezone.now()
    assert len(services.enqueue(make_content(), now=now)) == 1
    fetcher = net.Fetcher()
    assert services.process_due_jobs(now=now, fetcher=fetcher) == services.Summary()
    assert fetcher.calls == []
    later = now + timedelta(minutes=31)
    assert services.process_due_jobs(now=later, fetcher=fetcher).sent == 1


def test_templates_accept_only_known_placeholders_and_require_the_link():
    validate_template("Hi {title} {url} {site}")
    with pytest.raises(ValidationError, match="Unknown"):
        validate_template("{title} {url} {password}")
    with pytest.raises(ValidationError, match="url"):
        validate_template("{title}")


def test_long_titles_are_trimmed_but_the_link_survives(make_channel, make_content):
    channel = make_channel("x")
    text = services.render_text(channel, make_content(title="Acme " * 100))
    adapter = channels.get_adapter("x")
    assert adapter is not None
    assert len(text) <= adapter.message_limit
    assert "…" in text
    assert text.endswith("/firmwares/file/official/abc/")


def test_successful_delivery_marks_sent_with_the_external_id(
    make_channel, make_content, net
):
    make_channel("telegram")
    services.enqueue(make_content())
    fetcher = net.Fetcher(net.ok({"ok": True, "result": {"message_id": 77}}))
    assert services.process_due_jobs(fetcher=fetcher).sent == 1
    job = ShareJob.objects.get()
    assert (job.status, job.external_id, job.response_code) == ("sent", "77", "200")
    call = fetcher.calls[0]
    assert call["method"] == "POST"
    assert call["follow_redirects"] is False
    assert services.process_due_jobs(fetcher=fetcher).sent == 0
    assert len(fetcher.calls) == 1


def test_transient_failures_retry_with_backoff_then_give_up(
    make_channel, make_content, net
):
    channel = make_channel("webhook", max_attempts=3)
    services.enqueue(make_content())
    start = timezone.now()
    fetcher = net.Fetcher(net.reply(500), FetchTimeout("slow"), net.reply(502))
    summary = services.process_due_jobs(now=start, fetcher=fetcher)
    job = ShareJob.objects.get()
    assert summary.retried == 1 and job.status == "queued" and job.attempts == 1
    assert job.next_attempt_at == start + timedelta(seconds=RETRY_BACKOFF[0])
    soon = start + timedelta(seconds=30)
    assert services.process_due_jobs(now=soon, fetcher=fetcher).retried == 0
    second = start + timedelta(seconds=RETRY_BACKOFF[0] + 1)
    assert services.process_due_jobs(now=second, fetcher=fetcher).retried == 1
    third = second + timedelta(seconds=RETRY_BACKOFF[1] + 1)
    assert services.process_due_jobs(now=third, fetcher=fetcher).failed == 1
    job.refresh_from_db()
    channel.refresh_from_db()
    assert (job.status, job.attempts) == ("failed", 3)
    assert channel.consecutive_failures == 1
    assert ShareLog.objects.filter(job=job).count() >= 4


def test_rate_limit_responses_honour_retry_after(make_channel, make_content, net):
    make_channel("webhook")
    services.enqueue(make_content())
    now = timezone.now()
    services.process_due_jobs(
        now=now, fetcher=net.Fetcher(net.reply(429, **{"retry-after": "900"}))
    )
    assert ShareJob.objects.get().next_attempt_at == now + timedelta(seconds=900)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_terminal_immediately(
    status, make_channel, make_content, net
):
    make_channel("webhook")
    services.enqueue(make_content())
    assert services.process_due_jobs(fetcher=net.Fetcher(net.reply(status))).failed == 1
    assert ShareJob.objects.get().attempts == 1


def test_the_platform_reason_is_kept_without_secrets(make_channel, make_content, net):
    make_channel("telegram")
    services.enqueue(make_content())
    body = {"error": {"message": "Invalid token 123:BOT-TOKEN for page"}}
    fetcher = net.Fetcher(net.ok(body, status=400))
    services.process_due_jobs(fetcher=fetcher)
    error = ShareJob.objects.get().last_error
    assert "HTTP 400" in error and "Invalid token" in error
    assert "BOT-TOKEN" not in error


def test_blocked_destinations_and_bad_config_never_leak_secrets(
    make_channel, make_content, net
):
    channel = make_channel("telegram")
    services.enqueue(make_content())
    fetcher = net.Fetcher(BlockedDestination("host 123:BOT-TOKEN resolves to 10.0.0.1"))
    assert services.process_due_jobs(fetcher=fetcher).failed == 1
    job = ShareJob.objects.get()
    assert "123:BOT-TOKEN" not in job.last_error and "***" in job.last_error
    assert not any(
        "123:BOT-TOKEN" in line
        for line in ShareLog.objects.values_list("message", flat=True)
    )
    channel.refresh_from_db()
    assert "123:BOT-TOKEN" not in channel.last_error


def test_the_real_safefetcher_refuses_private_webhook_targets(
    make_channel, make_content
):
    make_channel("webhook", values={"endpoint_url": "https://127.0.0.1/hook"})
    services.enqueue(make_content())
    summary = services.process_due_jobs(fetcher=SafeFetcher())
    assert summary.failed == 1
    assert ShareJob.objects.get().response_code == "blocked"


def test_default_fetcher_is_the_seo_safefetcher():
    from apps.seo import fetcher as seo_fetcher

    assert services.default_fetcher is seo_fetcher.default_fetcher
    assert isinstance(services.default_fetcher(), SafeFetcher)


def test_ten_failures_in_a_row_open_the_circuit_breaker(
    make_channel, make_content, net
):
    channel = make_channel("webhook")
    for index in range(CHANNEL_TRIP_AFTER):
        services.enqueue(make_content(ref=f"official:{index}", title="x"))
    fetcher = net.Fetcher(*[net.reply(403)] * CHANNEL_TRIP_AFTER)
    for _ in range(CHANNEL_TRIP_AFTER):
        services.process_due_jobs(limit=1, fetcher=fetcher)
    channel.refresh_from_db()
    assert channel.enabled is False
    assert "Switched off" in channel.disabled_reason
    assert channel.state == "off"
    assert services.enqueue(make_content(ref="official:later")) == []


def test_a_success_resets_the_failure_counter(make_channel, make_content, net):
    channel = make_channel("webhook")
    services.enqueue(make_content(ref="official:a"))
    services.enqueue(make_content(ref="official:b"))
    services.process_due_jobs(limit=1, fetcher=net.Fetcher(net.reply(403)))
    channel.refresh_from_db()
    assert channel.consecutive_failures == 1
    services.process_due_jobs(fetcher=net.Fetcher(net.ok()))
    channel.refresh_from_db()
    assert channel.consecutive_failures == 0
    assert channel.last_success_at is not None


def test_a_job_is_leased_so_workers_do_not_double_send(make_channel, make_content, net):
    make_channel("webhook")
    (job,) = services.enqueue(make_content())
    fetcher = net.Fetcher(net.ok())
    now = timezone.now()
    assert services.attempt(job.pk, now=now, fetcher=fetcher) == "sent"
    assert services.attempt(job.pk, now=now, fetcher=fetcher) == ""
    assert len(fetcher.calls) == 1


def test_test_posts_work_while_off_and_never_trip_the_channel(make_channel, net):
    channel = make_channel("webhook")
    Channel.objects.filter(pk=channel.pk).update(enabled=False)
    channel.refresh_from_db()
    job = services.send_test(channel, fetcher=net.Fetcher(net.ok()))
    assert job.status == "sent" and job.content_kind == "test"
    failing = services.send_test(channel, fetcher=net.Fetcher(net.reply(500)))
    assert failing.status == "failed"
    channel.refresh_from_db()
    assert channel.consecutive_failures == 0
    bare = Channel.objects.create(name="bare", kind="telegram")
    broken = services.send_test(bare, fetcher=net.Fetcher())
    assert broken.status == "failed" and "Bot token" in broken.last_error


def test_staff_retry_and_cancel(make_channel, make_content, net):
    make_channel("webhook")
    services.enqueue(make_content())
    services.process_due_jobs(fetcher=net.Fetcher(net.reply(404)))
    job = ShareJob.objects.get()
    assert services.cancel_job(job) is False
    assert services.retry_job(job) is True
    assert (job.status, job.attempts) == ("queued", 0)
    assert services.retry_job(job) is False
    assert services.cancel_job(job) is True


def test_firmware_approved_event_queues_the_public_page_not_the_download(
    make_channel,
):
    make_channel("webhook")
    firmware = SimpleNamespace(
        brand=SimpleNamespace(name="Acme"),
        model=SimpleNamespace(name="Phone One"),
        variant=SimpleNamespace(name="Global"),
        build_number="B123",
        is_verified=True,
        access_tier="free",
    )
    payload = {
        "firmware": firmware,
        "firmware_type": "official",
        "firmware_id": "5f0e8b5e-1d62-4c1c-9d54-6f2a9f4f3a11",
    }
    event_bus.publish("firmware.approved", payload)
    event_bus.publish("firmware.approved", payload)
    job = ShareJob.objects.get()
    assert job.title == "Acme Phone One Global"
    assert job.url == f"{SITE}/firmwares/file/official/{payload['firmware_id']}/"
    assert "download" not in job.url
    assert job.text.startswith("New firmware: Acme Phone One Global")
    event_bus.publish("firmware.approved", {"firmware": None, "firmware_type": "x"})
    assert ShareJob.objects.count() == 1


def test_unverified_firmware_is_never_shared(make_channel):
    make_channel("webhook")
    firmware = SimpleNamespace(
        brand=SimpleNamespace(name="Acme"),
        model=SimpleNamespace(name="One"),
        variant=None,
        is_verified=False,
    )
    event_bus.publish(
        "firmware.approved",
        {"firmware": firmware, "firmware_type": "official", "firmware_id": "x"},
    )
    assert not ShareJob.objects.exists()


def test_published_blog_posts_are_shared_once_and_automation_is_skipped(
    make_channel, django_capture_on_commit_callbacks, django_user_model
):
    from apps.blog.models import Post, PostStatus

    make_channel("webhook")
    author = django_user_model.objects.create_user(
        username="writer", email="w@example.com"
    )

    def publish(title, **extra):
        with django_capture_on_commit_callbacks(execute=True):
            return Post.objects.create(
                title=title,
                body="<p>x</p>",
                author=author,
                status=PostStatus.PUBLISHED,
                publish_at=timezone.now() - timedelta(minutes=1),
                summary="A short summary",
                **extra,
            )

    post = publish("Human post")
    publish("Machine post", is_ai_generated=True)
    jobs = ShareJob.objects.filter(content_kind="blog")
    assert [j.object_ref for j in jobs] == [f"post:{post.pk}"]
    assert jobs[0].url == f"{SITE}{post.get_absolute_url()}"
    post.title = "Edited"
    with django_capture_on_commit_callbacks(execute=True):
        post.save()
    assert jobs.count() == 1


def test_gated_posts_share_a_teaser_never_the_body(
    make_channel, django_capture_on_commit_callbacks, django_user_model
):
    from apps.blog.models import Post, PostAccess, PostStatus

    make_channel("webhook", use_composer=True)
    author = django_user_model.objects.create_user(
        username="writer2", email="w2@example.com"
    )
    with django_capture_on_commit_callbacks(execute=True):
        Post.objects.create(
            title="Secret deep dive",
            body="<p>THE-PAID-BODY-TEXT</p>",
            author=author,
            status=PostStatus.PUBLISHED,
            publish_at=timezone.now() - timedelta(minutes=1),
            summary="Short public summary " * 20,
            access=PostAccess.FEATURED,
        )
    job = ShareJob.objects.get(content_kind="blog")
    assert "THE-PAID-BODY-TEXT" not in job.text
    assert "Featured article" in job.text


def test_shares_never_embed_secrets_in_the_queue(make_channel, make_content):
    make_channel("telegram")
    services.enqueue(make_content())
    job = ShareJob.objects.get()
    assert "BOT-TOKEN" not in job.text + job.url + job.title


def test_legacy_secret_layout_still_loads_and_sends(make_content, net):
    """Rows created before the rebuild stored one plain secret string."""
    channel = Channel(
        name="old",
        kind="telegram",
        enabled=True,
        target="@mobofirm",
        use_composer=False,
    )
    services.set_secret(channel, "123:BOT-TOKEN")
    channel.save()
    assert channel.state == "ready"
    services.enqueue(make_content())
    fetcher = net.Fetcher(net.ok({"ok": True, "result": {"message_id": 5}}))
    assert services.process_due_jobs(fetcher=fetcher).sent == 1
    assert fetcher.calls[0]["url"].endswith("/bot123:BOT-TOKEN/sendMessage")
