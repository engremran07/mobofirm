from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from apps.firmwares.chipsets import create_chipset_record
from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    FirmwareSourceType,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)
from apps.firmwares.tracking_models import FirmwareLinkReport


@pytest.mark.django_db(transaction=True)
def test_firmware_formats_unclassified_records_and_tools_render_responsively(
    live_server,
):
    samsung = Brand.objects.create(name="Samsung", slug="samsung")
    model = Model.objects.create(
        brand=samsung,
        name="Galaxy S24 Ultra",
        slug="galaxy-s24-ultra",
        model_code="SM-S928B",
    )
    OfficialFirmware.objects.create(
        original_file_name="fastboot-sample.tgz",
        stored_file_path="/firmware/fastboot-sample.tgz",
        brand=samsung,
        model=model,
        package_format=FirmwarePackageFormat.FASTBOOT_ROM,
        is_verified=True,
    )
    UnclassifiedFirmware.objects.create(
        original_file_name="unclassified-reviewed.zip",
        stored_file_path="/firmware/unclassified-reviewed.zip",
        brand=samsung,
        model=model,
        is_verified=True,
    )
    category = FlashingToolCategory.objects.create(
        name="Official utilities",
        slug="official-utilities",
    )
    tool = FlashingTool.objects.create(
        category=category,
        name="Vendor Utility",
        slug="vendor-utility",
        official_url="https://tools.example.test/vendor",
        is_active=True,
    )

    console_messages: list[tuple[str, str]] = []
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for firmware-catalog browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on(
            "console",
            lambda message: console_messages.append((message.type, message.text)),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        page.goto(
            f"{live_server.url}{reverse('firmwares:browse')}",
            wait_until="networkidle",
            timeout=15_000,
        )
        page.locator("#firmware-package-format").select_option("fastboot_rom")
        fastboot_row = page.locator("tbody tr").filter(has_text="fastboot-sample.tgz")
        expect(fastboot_row).to_be_visible()
        expect(fastboot_row).to_contain_text("Fastboot ROM")
        page.locator("#firmware-package-format").select_option("")
        page.locator("#firmware-type").select_option("unclassified")
        unclassified_row = page.locator("tbody tr").filter(
            has_text="unclassified-reviewed.zip"
        )
        expect(unclassified_row).to_be_visible()

        page.goto(
            f"{live_server.url}{reverse('firmwares:flashing_tool_detail', kwargs={'slug': tool.slug})}",
            wait_until="networkidle",
            timeout=15_000,
        )
        expect(
            page.get_by_role("link", name="Visit official tool source")
        ).to_have_attribute("href", tool.official_url)

        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1

        assert not page_errors
        assert not [
            item for item in console_messages if item[0] in {"error", "warning"}
        ]
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_unisoc_chipset_alias_notice_is_visible_without_layout_overflow(live_server):
    chipset = create_chipset_record(
        vendor="Unisoc",
        name="SC9863A",
        slug="unisoc-sc9863a",
        aliases=["Spreadtrum SC9863A", "RDA Microelectronics SC9863A"],
    )
    errors: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for firmware-catalog browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(
            f"{live_server.url}{reverse('firmwares:chipset_detail', kwargs={'slug': chipset.slug})}",
            wait_until="load",
        )
        expect(page.get_by_role("heading", name="About UNISOC naming")).to_be_visible()
        expect(page.get_by_text("Spreadtrum SC9863A")).to_be_visible()
        expect(page.get_by_text("RDA Microelectronics SC9863A")).to_be_visible()
        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1
        assert not errors
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_external_firmware_link_report_submits_from_the_public_detail_page(live_server):
    brand = Brand.objects.create(name="Northstar", slug="northstar")
    model = Model.objects.create(
        brand=brand,
        name="Atlas One",
        slug="atlas-one",
    )
    firmware = OfficialFirmware.objects.create(
        original_file_name="external-atlas.zip",
        source_type=FirmwareSourceType.EXTERNAL,
        external_url="https://downloads.example.invalid/atlas.zip",
        brand=brand,
        model=model,
        is_verified=True,
    )
    browser_errors: list[str] = []
    console_messages: list[tuple[str, str]] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for firmware-catalog browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.on(
            "console",
            lambda message: console_messages.append((message.type, message.text)),
        )
        page.goto(
            f"{live_server.url}{reverse('firmwares:firmware_detail', kwargs={'firmware_type': 'official', 'firmware_id': firmware.pk})}",
            wait_until="networkidle",
            timeout=15_000,
        )

        report = page.locator("details").filter(
            has=page.get_by_text("Report a broken download link")
        )
        expect(report).to_be_visible()
        report.locator("summary").click()
        report.locator("select[name='reason']").select_option("broken")
        report.locator("textarea[name='details']").fill(
            "The host says this file is unavailable."
        )
        report.get_by_role("button", name="Send report").click()

        expect(
            page.get_by_text("The external download report was sent for review.")
        ).to_be_visible()
        assert not browser_errors
        assert not [
            item for item in console_messages if item[0] in {"error", "warning"}
        ]
        for width in (390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            dimensions = page.evaluate(
                "() => ({viewport: document.documentElement.clientWidth, document: document.documentElement.scrollWidth})"
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1
        browser.close()
    assert FirmwareLinkReport.objects.filter(
        object_id=firmware.pk,
        reason=FirmwareLinkReport.Reason.BROKEN,
        details="The host says this file is unavailable.",
        status=FirmwareLinkReport.Status.OPEN,
    ).exists()
