from __future__ import annotations

import pytest
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright

from apps.firmwares.models import (
    Brand,
    FirmwarePackageFormat,
    FlashingTool,
    FlashingToolCategory,
    Model,
    OfficialFirmware,
    UnclassifiedFirmware,
)


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
