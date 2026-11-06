from __future__ import annotations

from pathlib import Path

import pytest
from django.urls import reverse

from apps.firmwares.models import Brand, Model, OfficialFirmware
from apps.storage import services
from apps.storage.models import FirmwareStorageLocation
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


def stage_firmware(root: Path, name: str, payload: bytes | None) -> OfficialFirmware:
    brand, _ = Brand.objects.get_or_create(
        name="Storage Browser", slug="storage-browser"
    )
    model, _ = Model.objects.get_or_create(brand=brand, name="SB1", slug="sb1")
    path = root / "official" / name
    if payload is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    firmware = OfficialFirmware.objects.create(
        original_file_name=name,
        stored_file_path=str(path),
        source_type="stored",
        brand=brand,
        model=model,
        is_verified=True,
        is_active=True,
    )
    services.sync_firmware_location(firmware)
    return firmware


@pytest.fixture
def firmware_root(settings, tmp_path: Path) -> Path:
    settings.FIRMWARE_STORAGE_ROOT = tmp_path.resolve()
    return settings.FIRMWARE_STORAGE_ROOT


@pytest.mark.django_db(transaction=True)
def test_staff_storage_status_has_no_console_csp_or_overflow_problems(
    live_server, firmware_root
):
    stage_firmware(firmware_root, "present.zip", b"payload")
    stage_firmware(firmware_root, "missing.zip", None)
    cookie = login_cookie(make_user("storage_browser_staff", staff=True))
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, [reverse("storage:status")], failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_verifying_pending_locations_reports_health_and_names_disabled_providers(
    live_server, firmware_root
):
    stage_firmware(firmware_root, "present.zip", b"payload")
    stage_firmware(firmware_root, "missing.zip", None)
    cookie = login_cookie(make_user("storage_browser_verifier", staff=True))
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('storage:status')}")
        ready(page)

        providers = page.get_by_role("heading", name="Storage providers").locator("..")
        assert providers.get_by_text("Disabled").first.is_visible()
        page.get_by_role("button", name="pending").click()
        page.get_by_text("Verified 2 pending location(s).").wait_for(timeout=10_000)
        assert page.get_by_role("table").is_visible()
        assert page.get_by_role(
            "region", name="Firmware storage locations"
        ).is_visible()

    assert not failures, "\n".join(failures)
    statuses = set(FirmwareStorageLocation.objects.values_list("status", flat=True))
    assert statuses == {
        FirmwareStorageLocation.Status.AVAILABLE,
        FirmwareStorageLocation.Status.MISSING,
    }


@pytest.mark.django_db(transaction=True)
def test_members_cannot_open_the_storage_status_page(live_server):
    cookie = login_cookie(make_user("storage_browser_member"))
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        page.goto(f"{live_server.url}{reverse('storage:status')}")
        assert page.get_by_role("heading", name="Storage status").count() == 0
        assert "/storage/" not in page.url.split("?")[0]
