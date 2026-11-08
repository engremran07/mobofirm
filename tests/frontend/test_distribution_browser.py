"""Browser checks for the distribution admin: dashboard, channel forms, calendar,
approvals, analytics.

They run inside the Unfold admin shell with its CSP: no console/CSP errors, no
horizontal overflow at 360/768/1365 px, light and dark themes, and the real
interactions (run queue, test connection, approve, cancel) work without native dialogs.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth.models import Permission
from django.db.models import Model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from apps.distribution import services
from apps.distribution.models import Channel, ClickEvent, ShareJob, ShortLink
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


def distribution_staff(username: str):
    user = make_user(username, staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="distribution")
    )
    return user


def channel(kind: str, name: str, values: dict[str, str], **extra) -> Channel:
    row = Channel(kind=kind, name=name, enabled=True, **extra)
    services.apply_values(row, values)
    row.save()
    return row


def seed() -> dict[str, Model]:
    # A webhook on a private address: SafeFetcher refuses it, so "Test connection"
    # fails fast and nothing ever leaves the machine.
    hooks = channel(
        "webhook",
        "Hooks",
        {"endpoint_url": "https://127.0.0.1/hook"},
        use_composer=False,
        consecutive_failures=3,
    )
    telegram = channel(
        "telegram",
        "News channel",
        {"chat_id": "@mobofirm", "bot_token": "123:BOT-TOKEN"},
        approval_mode="approval",
    )
    youtube = channel(
        "youtube",
        "Shorts",
        {
            "client_id": "cid-1234",
            "client_secret": "csecret-1234",
            "refresh_token": "r-1234",
        },
    )
    now = timezone.now()

    def job(chan, ref, status, **extra) -> ShareJob:
        return ShareJob.objects.create(
            channel=chan,
            content_kind="firmware",
            object_ref=ref,
            idempotency_key=f"{chan.pk}:firmware:{ref}",
            title=f"Acme Phone {ref}",
            url="https://mobofirm.example/go/abc/",
            text=f"Looking for Acme {ref} firmware?\nhttps://mobofirm.example/go/abc/",
            **{"status": status, "next_attempt_at": now + timedelta(hours=3), **extra},
        )

    pending = job(telegram, "one", "pending")
    queued = job(hooks, "two", "queued")
    waiting = job(
        youtube,
        "three",
        "needs_media",
        last_error="Needs a video: ffmpeg was not found.",
    )
    sent = job(telegram, "four", "sent", sent_at=now - timedelta(hours=2))
    link = ShortLink.objects.create(
        code="abc2345",
        job=sent,
        channel=telegram,
        content_kind="firmware",
        object_ref="four",
        hook="question",
        target_url="https://mobofirm.example/x/",
        clicks=3,
    )
    for hours in (1, 2, 30):
        ClickEvent.objects.create(link=link, bucket=now - timedelta(hours=hours))
    return {
        "hooks": hooks,
        "telegram": telegram,
        "youtube": youtube,
        "pending": pending,
        "queued": queued,
        "waiting": waiting,
    }


def pages(rows: dict[str, Model]) -> list[str]:
    return [
        reverse("admin:distribution_channel_dashboard"),
        reverse("admin:distribution_channel_calendar"),
        reverse("admin:distribution_channel_review"),
        reverse("admin:distribution_channel_analytics"),
        reverse("admin:distribution_channel_platforms"),
        f"{reverse('admin:distribution_channel_add')}?kind=youtube",
        f"{reverse('admin:distribution_channel_add')}?kind=telegram",
        reverse("admin:distribution_channel_change", args=[rows["telegram"].pk]),
        reverse("admin:distribution_channel_preview", args=[rows["telegram"].pk]),
        reverse("admin:distribution_channel_changelist"),
        reverse("admin:distribution_sharejob_changelist"),
    ]


@pytest.mark.django_db(transaction=True)
def test_distribution_pages_render_cleanly_in_light_and_dark(live_server):
    user = distribution_staff("dist_shell")
    rows = seed()
    cookie = login_cookie(user)
    paths = pages(rows)
    failures: list[str] = []
    with chromium() as browser:
        for scheme in ("light", "dark"):
            context = session_context(browser, live_server, cookie)
            context.add_init_script(
                f"window.localStorage.setItem('adminTheme', '\"{scheme}\"')"
            )
            page = context.new_page()
            watch(page, failures)
            visit_all(page, live_server, paths, failures)
            page.set_viewport_size({"width": 1365, "height": 900})
            for path in paths:
                page.goto(f"{live_server.url}{path}")
                ready(page)
                if page.locator("nav a[href]").count() < 3:
                    failures.append(f"{path}: admin sidebar is missing ({scheme})")
                if page.locator(".site-header, .site-container").count():
                    failures.append(f"{path}: public shell markup rendered")
                dark = page.evaluate(
                    "() => document.documentElement.classList.contains('dark')"
                )
                if dark != (scheme == "dark"):
                    failures.append(f"{path}: expected {scheme} theme")
            page.goto(f"{live_server.url}{pages(rows)[0]}")
            ready(page)
            sidebar = page.locator("nav")
            for label in ("Content calendar", "Approval queue", "Link analytics"):
                if not sidebar.get_by_role("link", name=label).count():
                    failures.append(f"sidebar link missing: {label} ({scheme})")
            context.close()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_dashboard_actions_and_channel_form(live_server):
    user = distribution_staff("dist_actions")
    rows = seed()
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}{reverse('admin:distribution_channel_dashboard')}")
        ready(page)
        expect(page.locator("h1.mf-ui-title")).to_have_text("Distribution")
        expect(page.get_by_text("Circuit breaker")).to_be_visible()
        expect(page.get_by_text("3 / 10")).to_be_visible()

        page.get_by_role("button", name="Run queue now").click()
        expect(page.get_by_text("Queue run:")).to_be_visible()

        row = page.get_by_role("row").filter(has_text="Hooks")
        row.get_by_role("button", name="Test connection").click()
        expect(page.get_by_text("connection failed")).to_be_visible()

        page.goto(f"{live_server.url}{reverse('admin:distribution_channel_platforms')}")
        page.get_by_role("link", name="Add YouTube (Shorts upload)").click()
        page.wait_for_url("**kind=youtube")
        for label in ("OAuth client id", "OAuth client secret", "Refresh token"):
            expect(page.get_by_label(label).first).to_be_visible()
        expect(page.get_by_text("youtube.upload").first).to_be_visible()

        page.goto(
            f"{live_server.url}"
            f"{reverse('admin:distribution_channel_preview', args=[rows['telegram'].pk])}"
        )
        expect(page.get_by_text("Dry run")).to_be_visible()
        expect(page.get_by_text("/ 4096 characters").first).to_be_visible()
    assert not native, f"native dialogs were used: {native}"
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_approve_and_cancel_from_the_review_and_calendar_pages(live_server):
    user = distribution_staff("dist_review")
    rows = seed()
    cookie = login_cookie(user)
    failures: list[str] = []
    native: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, native)
        page.goto(f"{live_server.url}{reverse('admin:distribution_channel_review')}")
        ready(page)
        box = page.get_by_label("Post text (edit before it is sent)")
        box.fill("Edited in the browser https://mobofirm.example/go/abc/")
        page.get_by_role("button", name="Approve").click()
        expect(page.get_by_text("Approved")).to_be_visible()

        page.goto(f"{live_server.url}{reverse('admin:distribution_channel_calendar')}")
        ready(page)
        expect(page.get_by_text("Awaiting approval").first).not_to_be_visible()
        expect(page.get_by_text("Needs media")).to_be_visible()
        row = page.get_by_role("row").filter(has_text="Acme Phone two")
        row.get_by_role("button", name="Cancel").click()
        expect(page.get_by_text("Cancelled")).to_be_visible()
    approved = ShareJob.objects.get(pk=rows["pending"].pk)
    assert approved.status == "queued" and approved.text.startswith(
        "Edited in the browser"
    )
    assert ShareJob.objects.get(pk=rows["queued"].pk).status == "cancelled"
    assert ShareJob.objects.filter(status="cancelled").count() == 1
    assert not native, f"native dialogs were used: {native}"
    assert not failures, "\n".join(failures)
