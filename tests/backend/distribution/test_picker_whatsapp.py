"""Platform picker (menu, search, setup guide), WhatsApp (Cloud API opt-in rules, 24 hour
window, webhook) and the assisted WhatsApp Channel mode."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.distribution import catalog, channels, services, whatsapp
from apps.distribution.channels import MediaRefs, SendContext
from apps.distribution.models import Channel, ShareJob, WhatsAppRecipient

pytestmark = pytest.mark.django_db
LINK = "https://mobofirm.example/go/abc2345/"
PICKER = "admin:distribution_channel_platforms"


def ctx_for(channel, fetcher, **job):
    stored = services.stored_values(channel)
    secrets = [stored.get(f.name, "") for f in channel.adapter.secret_fields()]
    row = ShareJob(
        channel=channel,
        content_kind="firmware",
        idempotency_key="k1",
        title="Acme Phone One",
        url=LINK,
        text=f"New firmware\n{LINK}",
        **job,
    )
    return SendContext(
        channel, row, stored, channels.Http(fetcher, secrets), MediaRefs()
    )


def sections(response):
    return [s.category.key for s in response.context["sections"]]


# ------------------------------------------------------------- the menu ----


def test_catalogue_covers_every_registered_platform_exactly():
    assert set(catalog.INFO) == set(channels.adapters())
    for info in catalog.INFO.values():
        assert info.categories and all(
            c in catalog.CATEGORY_BY_KEY for c in info.categories
        )
        assert info.steps and info.summary and info.auth


def test_picker_groups_platforms_by_purpose(admin_client):
    response = admin_client.get(reverse(PICKER))
    assert sections(response) == [
        "social",
        "video",
        "messaging",
        "fediverse",
        "developer",
    ]
    by_key = {
        s.category.key: {c.kind for c in s.cards} for s in response.context["sections"]
    }
    assert by_key["messaging"] == {
        "telegram",
        "whatsapp",
        "whatsapp_channel",
        "discord",
        "slack",
        "reddit",
    }
    assert by_key["fediverse"] == {"mastodon", "bluesky"}
    assert by_key["developer"] == {"webhook"}
    assert {"tiktok"} <= by_key["social"] & by_key["video"]
    assert "youtube" in by_key["video"] and "x" in by_key["social"]


def test_whatsapp_is_offered_twice_with_honest_labels(admin_client):
    body = admin_client.get(reverse(PICKER)).content.decode()
    assert "WhatsApp (Cloud API)" in body and "WhatsApp Channel (assisted)" in body
    assert "Assisted: posted by hand" in body
    assert "?kind=whatsapp" in body and "?kind=whatsapp_channel" in body


def test_category_and_search_filters(admin_client):
    only = admin_client.get(reverse(PICKER), {"cat": "fediverse"})
    assert sections(only) == ["fediverse"]
    found = admin_client.get(reverse(PICKER), {"q": "whats"})
    kinds = {c.kind for s in found.context["sections"] for c in s.cards}
    assert kinds == {"whatsapp", "whatsapp_channel"}
    assert not admin_client.get(reverse(PICKER), {"q": "zzzz"}).context["sections"]
    junk = admin_client.get(reverse(PICKER), {"cat": "nope"})
    assert junk.context["cat"] == "all"


def test_htmx_search_swaps_only_the_results_region(admin_client):
    response = admin_client.get(
        reverse(PICKER),
        {"q": "telegram"},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="dist-picker-results",
    )
    body = response.content.decode()
    assert 'id="dist-picker-results"' in body and "<html" not in body
    assert "Telegram" in body and "Mastodon" not in body


def test_connected_platforms_get_their_own_section(admin_client, make_channel):
    make_channel("telegram", name="News TG")
    response = admin_client.get(reverse(PICKER))
    assert [c.kind for c in response.context["connected"]] == ["telegram"]
    body = response.content.decode()
    assert "News TG" in body and "Add another" in body


def test_setup_guide_is_a_dialog_over_htmx_and_a_page_otherwise(admin_client):
    url = reverse("admin:distribution_channel_requirements", args=["whatsapp"])
    dialog = admin_client.get(url, HTTP_HX_REQUEST="true").content.decode()
    assert "data-console-dialog-content" in dialog and "<html" not in dialog
    assert "developers.facebook.com/docs/whatsapp" in dialog
    assert "STOP" in dialog and "24 hour" in dialog
    page = admin_client.get(url).content.decode()
    assert "console-staff-page" in page and "How to get your credentials" in page
    assert (
        admin_client.get(
            reverse("admin:distribution_channel_requirements", args=["nope"])
        ).status_code
        == 404
    )


def test_picker_and_guide_need_the_add_permission(staff_client):
    url = reverse("admin:distribution_channel_requirements", args=["telegram"])
    viewer = staff_client("view_channel")
    assert viewer.get(reverse(PICKER)).status_code == 403
    assert viewer.get(url).status_code == 403
    adder = staff_client("add_channel", "view_channel")
    assert adder.get(reverse(PICKER)).status_code == 200
    assert adder.get(url).status_code == 200


# ------------------------------------------------------------- stepper ----

ADD_BASE = {
    "name": "WA channel",
    "kind": "whatsapp_channel",
    "cred_channel_url": "https://whatsapp.com/channel/0029VaTest",
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


def test_add_form_is_a_five_step_flow(admin_client):
    body = admin_client.get(
        f"{reverse('admin:distribution_channel_add')}?kind=whatsapp_channel"
    ).content.decode()
    for step in (
        "1 · Platform",
        "2 · Credentials",
        "3 · Content rules",
        "4 · Schedule &amp; approval",
        "5 · Test &amp; status",
    ):
        assert step in body
    assert "channel_url" in body


@pytest.mark.parametrize("htmx", [False, True])
def test_saving_a_new_channel_lands_on_the_test_step(admin_client, htmx):
    extra = {"HTTP_HX_REQUEST": "true"} if htmx else {}
    response = admin_client.post(
        f"{reverse('admin:distribution_channel_add')}?kind=whatsapp_channel",
        {**ADD_BASE, "_save": "1"},
        **extra,
    )
    channel = Channel.objects.get(name="WA channel")
    assert response.status_code == 302
    assert response.url == reverse(
        "admin:distribution_channel_change", args=[channel.pk]
    )
    assert not channel.enabled


def test_assisted_channel_rejects_a_foreign_link(admin_client):
    response = admin_client.post(
        f"{reverse('admin:distribution_channel_add')}?kind=whatsapp_channel",
        {
            **ADD_BASE,
            "enabled": "on",
            "cred_channel_url": "https://evil.example/channel/1",
            "_save": "1",
        },
    )
    assert response.status_code == 200 and not Channel.objects.exists()


# ------------------------------------------------- assisted WhatsApp Channel ----


def test_assisted_channel_hands_off_and_is_never_reported_sent(make_channel, net):
    channel = make_channel("whatsapp_channel")
    job = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref="a",
        idempotency_key="a1",
        title="T",
        url=LINK,
        text="copy me",
        status=ShareJob.Status.QUEUED,
    )
    fetcher = net.Fetcher()
    assert services.attempt(job.pk, fetcher=fetcher) == "handoff"
    job.refresh_from_db()
    assert job.status == ShareJob.Status.HANDOFF and job.sent_at is None
    assert fetcher.calls == []  # nothing is ever sent
    channel.refresh_from_db()
    assert channel.last_success_at is None


def test_mark_posted_is_idempotent_and_counts_as_success(make_channel):
    channel = make_channel("whatsapp_channel")
    job = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref="a",
        idempotency_key="a1",
        title="T",
        url=LINK,
        text="copy me",
        status=ShareJob.Status.HANDOFF,
    )
    assert services.mark_posted(job)
    assert not services.mark_posted(job)
    job.refresh_from_db()
    channel.refresh_from_db()
    assert job.status == ShareJob.Status.SENT and channel.last_success_at is not None


def test_handoff_page_and_posted_action_follow_permissions(
    admin_client, staff_client, make_channel
):
    channel = make_channel("whatsapp_channel")
    job = ShareJob.objects.create(
        channel=channel,
        content_kind="firmware",
        object_ref="a",
        idempotency_key="a1",
        title="Pixel fw",
        url=LINK,
        text="copy me please",
        status=ShareJob.Status.HANDOFF,
    )
    page = admin_client.get(
        reverse("admin:distribution_channel_handoff")
    ).content.decode()
    assert "copy me please" in page and "Mark as posted" in page
    assert "https://whatsapp.com/channel/0029VaTest" in page
    posted = reverse("admin:distribution_channel_job_posted", args=[job.pk])
    reader = staff_client("view_sharejob")
    assert reader.get(reverse("admin:distribution_channel_handoff")).status_code == 200
    assert reader.post(posted).status_code == 403
    assert admin_client.get(posted).status_code == 405
    response = admin_client.post(posted)
    assert response.status_code == 302
    job.refresh_from_db()
    assert job.status == ShareJob.Status.SENT


def test_test_post_on_an_assisted_channel_is_prepared_not_sent(make_channel, net):
    channel = make_channel("whatsapp_channel")
    job = services.send_test(channel, fetcher=net.Fetcher())
    assert job.status == ShareJob.Status.HANDOFF


# ------------------------------------------------- WhatsApp Cloud API rules ----


def test_numbers_are_normalised_and_deduplicated():
    assert whatsapp.normalise_numbers("+1 555 123 4567, 15551234567; bad") == [
        "+15551234567"
    ]


def test_opted_out_numbers_are_never_messaged_even_while_listed(make_channel, net):
    channel = make_channel("whatsapp")
    person = whatsapp.audience(channel, ["+15551234567"])[0]
    whatsapp.opt_out(person, "replied STOP")
    fetcher = net.Fetcher(net.ok({"messages": [{}]}))
    outcome = channel.adapter.publish(ctx_for(channel, fetcher))
    assert outcome.ok
    assert [json.loads(c["body"])["to"] for c in fetcher.calls] == ["+4915112345678"]


def test_everyone_opted_out_is_a_terminal_config_failure(make_channel, net):
    channel = make_channel("whatsapp", values={"recipients": "+15551234567"})
    whatsapp.opt_out(whatsapp.audience(channel, ["+15551234567"])[0], "replied STOP")
    outcome = channel.adapter.publish(ctx_for(channel, net.Fetcher()))
    assert not outcome.ok and not outcome.retryable and outcome.code == "config"


def test_free_text_only_inside_the_24_hour_window(make_channel, net):
    channel = make_channel("whatsapp")
    inside, outside = whatsapp.audience(channel, ["+15551234567", "+4915112345678"])
    inside.last_inbound_at = timezone.now() - timedelta(hours=2)
    inside.save()
    outside.last_inbound_at = timezone.now() - timedelta(hours=25)
    outside.save()
    fetcher = net.Fetcher(net.ok({"messages": [{}]}), net.ok({"messages": [{}]}))
    assert channel.adapter.publish(ctx_for(channel, fetcher)).ok
    sent = {b["to"]: b for b in fetcher.bodies()}
    assert (
        sent["+15551234567"]["type"] == "text"
        and LINK in sent["+15551234567"]["text"]["body"]
    )
    assert sent["+4915112345678"]["type"] == "template"


def test_a_refusal_for_one_person_does_not_block_the_rest(make_channel, net):
    channel = make_channel("whatsapp")
    refusal = net.ok(
        {"error": {"code": 131050, "message": "user stopped marketing messages"}}, 400
    )
    fetcher = net.Fetcher(refusal, net.ok({"messages": [{}]}))
    ctx = ctx_for(channel, fetcher)
    outcome = channel.adapter.publish(ctx)
    assert outcome.ok and outcome.external_id == "1 recipients"
    refused = WhatsAppRecipient.objects.get(channel=channel, phone="+15551234567")
    assert not refused.active and ctx.state["failed_to"] == ["+15551234567"]
    # a retry of the same job never messages anyone twice
    again = net.Fetcher()
    assert channel.adapter.publish(ctx_for(channel, again, remote_state=ctx.state)).ok
    assert again.calls == []


def test_every_recipient_refused_fails_the_job(make_channel, net):
    channel = make_channel("whatsapp")
    bad = net.ok({"error": {"code": 131026, "message": "undeliverable"}}, 400)
    outcome = channel.adapter.publish(ctx_for(channel, net.Fetcher(bad, bad)))
    assert not outcome.ok and not outcome.retryable


# ---------------------------------------------------------------- webhook ----


def signed(secret, payload):
    body = json.dumps(payload).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, f"sha256={digest}"


def hook(channel):
    return reverse("distribution:whatsapp_webhook", args=[channel.pk])


def event(**value):
    meta = {"phone_number_id": "777"}
    return {"entry": [{"changes": [{"value": {"metadata": meta, **value}}]}]}


@pytest.fixture(name="wa")
def _wa_channel(make_channel):
    channel = make_channel(
        "whatsapp", values={"app_secret": "app-secret-1", "verify_token": "verify-me-1"}
    )
    whatsapp.audience(channel, ["+15551234567", "+4915112345678"])
    return channel


def test_webhook_handshake_needs_the_verify_token(client, wa):
    ok = client.get(
        hook(wa),
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me-1",
            "hub.challenge": "12345",
        },
    )
    assert ok.status_code == 200 and ok.content == b"12345"
    bad = client.get(
        hook(wa),
        {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert bad.status_code == 403


def test_webhook_rejects_unsigned_or_wrongly_signed_events(client, wa):
    body, _good = signed("app-secret-1", event())
    assert (
        client.post(hook(wa), body, content_type="application/json").status_code == 403
    )
    _other, wrong = signed("another-secret", event())
    assert (
        client.post(
            hook(wa),
            body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=wrong,
        ).status_code
        == 403
    )


def test_webhook_is_closed_without_an_app_secret(client, make_channel):
    channel = make_channel("whatsapp", name="plain")
    assert (
        client.post(hook(channel), b"{}", content_type="application/json").status_code
        == 404
    )
    assert client.get(hook(channel), {"hub.mode": "subscribe"}).status_code == 403


def test_webhook_for_a_non_whatsapp_channel_is_unknown(client, make_channel):
    assert (
        client.post(
            hook(make_channel("telegram")), b"{}", content_type="application/json"
        ).status_code
        == 404
    )


def test_stop_reply_opts_out_and_start_opts_back_in(client, wa):
    stop = event(messages=[{"from": "15551234567", "text": {"body": " STOP "}}])
    body, sig = signed("app-secret-1", stop)
    assert (
        client.post(
            hook(wa),
            body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sig,
        ).status_code
        == 200
    )
    person = WhatsAppRecipient.objects.get(channel=wa, phone="+15551234567")
    assert not person.active and person.last_inbound_at is not None
    start = event(messages=[{"from": "15551234567", "text": {"body": "start"}}])
    body, sig = signed("app-secret-1", start)
    client.post(
        hook(wa), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig
    )
    person.refresh_from_db()
    assert person.active and person.opt_in_source == "replied START"


def test_status_events_update_delivery_and_platform_opt_out(client, wa):
    delivered = event(statuses=[{"recipient_id": "15551234567", "status": "delivered"}])
    body, sig = signed("app-secret-1", delivered)
    client.post(
        hook(wa), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig
    )
    assert (
        WhatsAppRecipient.objects.get(phone="+15551234567").last_status == "delivered"
    )
    failed = event(
        statuses=[
            {
                "recipient_id": "4915112345678",
                "status": "failed",
                "errors": [{"code": 131050}],
            }
        ]
    )
    body, sig = signed("app-secret-1", failed)
    client.post(
        hook(wa), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig
    )
    other = WhatsAppRecipient.objects.get(phone="+4915112345678")
    assert other.last_status == "failed" and not other.active


def test_unknown_numbers_and_foreign_phone_ids_are_ignored(wa):
    unknown = event(messages=[{"from": "19998887777", "text": {"body": "STOP"}}])
    assert whatsapp.handle_payload(wa, unknown) == 0
    foreign = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "other"},
                            "messages": [
                                {"from": "15551234567", "text": {"body": "STOP"}}
                            ],
                        }
                    }
                ]
            }
        ]
    }
    assert whatsapp.handle_payload(wa, foreign) == 0
    assert WhatsAppRecipient.objects.get(phone="+15551234567").active
