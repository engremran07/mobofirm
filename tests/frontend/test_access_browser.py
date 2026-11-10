"""Browser checks: My files Active/History tabs and the access type dialogs."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Browser, Page

from apps.firmwares import access_requests
from apps.firmwares.access import finalize_served_download
from apps.firmwares.access_change_models import AccessChangeLog, AccessChangeRequest
from apps.firmwares.models import (
    Brand,
    Model,
    OfficialFirmware,
    Variant,
)
from apps.shop import services
from apps.shop.models import PackageTier, UserPackage
from tests.frontend.browser_support import (
    chromium,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
    session_context,
    watch,
)

BROWSER_LOCK = Path(__file__).resolve().parents[2] / ".browser_lock"
LOCK_WAIT_SECONDS = 600
LOCK_RETRY_SECONDS = 10
WIDTHS = (360, 768, 1365)


@contextmanager
def locked_chromium() -> Iterator[Browser]:
    """One Chromium at a time on this machine: hold the shared lock directory."""
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            BROWSER_LOCK.mkdir()
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                pytest.fail("The browser lock stayed busy for 10 minutes.")
            time.sleep(LOCK_RETRY_SECONDS)
    try:
        with chromium() as browser:
            yield browser
    finally:
        BROWSER_LOCK.rmdir()


def listed(page: Page) -> list[str]:
    """File names shown in the library table."""
    names = page.locator(".library-file-name")
    names.first.wait_for(state="attached", timeout=10_000)
    return [text.strip() for text in names.all_text_contents()]


def firmware_row(name: str, tier: str, uploader: Any = None) -> OfficialFirmware:
    brand, _ = Brand.objects.get_or_create(slug="acme", defaults={"name": "Acme"})
    model, _ = Model.objects.get_or_create(
        brand=brand, slug="phone-one", defaults={"name": "Phone One"}
    )
    variant, _ = Variant.objects.get_or_create(
        model=model, slug="global", defaults={"name": "Global"}
    )
    return OfficialFirmware.objects.create(
        original_file_name=name,
        stored_file_path=f"/firmware/{name}",
        brand=brand,
        model=model,
        variant=variant,
        is_verified=True,
        is_active=True,
        access_tier=tier,
        uploader=uploader,
    )


@pytest.mark.django_db(transaction=True)
def test_library_tabs_split_active_files_from_read_only_history(live_server) -> None:
    member = make_user("tab_member")
    gold = PackageTier.objects.create(
        name="Gold",
        slug="gold",
        price=Decimal("9.00"),
        duration_days=30,
        bandwidth_enabled=False,
        limit_devices=False,
        applies_to_featured=True,
    )
    services.activate_package(member, gold)
    featured = firmware_row("members-only.zip", "featured")
    free = firmware_row("open-file.zip", "free")
    finalize_served_download(member, featured)
    finalize_served_download(member, free)
    UserPackage.objects.filter(user=member, is_baseline=False).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    cookies = login_cookie(member)
    failures: list[str] = []
    natives: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, natives)
        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(f"{live_server.url}{reverse('library:my_files')}")
            ready(page)
            assert listed(page) == ["open-file.zip"]
            assert page.locator("[data-library-tab=active]").get_attribute(
                "aria-current"
            )

            page.locator("[data-library-tab=history]").click()
            page.wait_for_url("**tab=history")
            ready(page)
            assert listed(page) == ["members-only.zip"]
            assert page.get_by_role("link", name="Download again").count() == 0
            assert page.locator("[data-history-reason]").first.is_visible()
            assert (
                "Package expired"
                in page.locator("[data-history-reason]").first.inner_text()
            )
            assert page.locator("[data-renew-package]").first.is_visible()

            page.locator("#library-q").fill("members")
            page.wait_for_function(
                "() => document.querySelectorAll('#library-results tbody tr').length === 1"
            )
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, width
        context.close()

    assert failures == []
    assert natives == []


@pytest.mark.django_db(transaction=True)
def test_staff_change_the_access_type_from_the_firmware_dialog(live_server) -> None:
    staff = make_user("dialog_staff", staff=True)
    staff.is_superuser = True
    staff.save()
    firmware = firmware_row("promote-me.zip", "free", uploader=make_user("dialog_up"))
    cookies = login_cookie(staff)
    detail = reverse(
        "firmwares:firmware_detail",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )
    failures: list[str] = []
    natives: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, natives)
        page.goto(f"{live_server.url}{detail}")
        ready(page)

        page.locator("[data-access-change]").click()
        dialog = page.locator("#firmware-access-dialog")
        dialog.wait_for(state="visible")
        assert dialog.get_by_role("heading", name="Change the access type").is_visible()

        page.keyboard.press("Escape")
        dialog.wait_for(state="hidden")

        page.locator("[data-access-change]").click()
        dialog = page.locator("#firmware-access-dialog")
        dialog.wait_for(state="visible")
        dialog.locator("#id_access_tier").select_option("paid")
        dialog.locator("#id_reason").fill("Rare build worth selling")
        dialog.get_by_role("button", name="Change access type").click()
        dialog.get_by_text("A paid file needs a price.").wait_for(state="visible")

        dialog.locator("#id_access_tier").select_option("featured")
        dialog.locator("#id_price").fill("")
        dialog.get_by_role("button", name="Change access type").click()
        page.wait_for_function(
            "() => document.body.innerText.includes('Featured')", timeout=10_000
        )
        context.close()

    assert failures == []
    assert natives == []
    firmware.refresh_from_db()
    assert firmware.access_tier == "featured"
    log = AccessChangeLog.objects.get()
    assert (log.reason, log.source) == ("Rare build worth selling", "dialog")


@pytest.mark.django_db(transaction=True)
def test_an_uploader_requests_a_change_from_the_dialog(
    live_server, monkeypatch
) -> None:
    monkeypatch.setattr(access_requests, "is_seller", lambda user: True)
    uploader = make_user("request_up")
    firmware = firmware_row("ask-me.zip", "free", uploader=uploader)
    cookies = login_cookie(uploader)
    detail = reverse(
        "firmwares:firmware_detail",
        kwargs={"firmware_type": "official", "firmware_id": firmware.pk},
    )
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies, width=390)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{detail}")
        ready(page)
        assert page.locator("[data-access-change]").count() == 0

        page.locator("[data-access-request]").click()
        dialog = page.locator("#firmware-access-request-dialog")
        dialog.wait_for(state="visible")
        dialog.locator("#id_access_tier").select_option("featured")
        dialog.locator("#id_reason").fill("Please offer this build to members")
        dialog.get_by_role("button", name="Send request").click()
        page.wait_for_function(
            "() => document.body.innerText.includes('waiting for staff')",
            timeout=10_000,
        )
        context.close()

    assert failures == []
    request = AccessChangeRequest.objects.get()
    assert (request.to_tier, request.status) == ("featured", "pending")
    firmware.refresh_from_db()
    assert firmware.access_tier == "free"
