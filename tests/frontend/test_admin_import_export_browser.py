"""Browser checks of the staff-console import and export pages (brands).

Upload -> dry-run preview -> confirm, then the export page, at 360 and 1280 px with
zero console errors and no horizontal overflow.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import FilePayload, expect

from apps.firmwares.models import Brand
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    overflow,
    session_context,
    watch,
)

CSV = b"slug,name,website_url\nacme,Acme,https://acme.example\nbeta,Beta Mobile,\n"
BAD_CSV = b"slug,name,website_url\nugly,Ugly,http://not-https.example\n"


def test_import_preview_confirm_and_export_pages(live_server):
    admin = get_user_model().objects.create_superuser(
        username="io-browser", email="io-browser@example.test", password="x"
    )
    cookies = login_cookie(admin)
    failures: list[str] = []
    base = live_server.url
    import_url = f"{base}{reverse('admin:firmwares_brand_import')}"
    export_url = f"{base}{reverse('admin:firmwares_brand_export')}"

    with chromium() as browser:
        for width in (1280, 360):
            context = session_context(browser, live_server, cookies, width=width)
            page = context.new_page()
            watch(page, failures)

            page.goto(import_url, wait_until="networkidle")
            expect(page.get_by_role("heading", level=1)).to_contain_text("Import")
            page.set_input_files(
                "input[type=file]",
                files=[
                    FilePayload(name="bad.csv", mimeType="text/csv", buffer=BAD_CSV)
                ],
            )
            page.get_by_role("button", name="Preview import").click()
            expect(page.locator("[data-import-preview]")).to_contain_text(
                "1 with errors"
            )
            expect(page.locator("[data-import-preview]")).to_contain_text(
                "valid https URL"
            )
            expect(page.get_by_role("button", name="Confirm and import")).to_have_count(
                0
            )
            dims = overflow(page)
            assert dims["document"] <= dims["viewport"] + 1, (width, "preview errors")

            page.goto(import_url, wait_until="networkidle")
            if width == 1280:
                page.set_input_files(
                    "input[type=file]",
                    files=[
                        FilePayload(name="brands.csv", mimeType="text/csv", buffer=CSV)
                    ],
                )
                page.get_by_role("button", name="Preview import").click()
                expect(page.locator("[data-import-preview]")).to_contain_text(
                    "2 to create"
                )
                page.get_by_role("button", name="Confirm and import").click()
                expect(page).to_have_url(
                    f"{base}{reverse('admin:firmwares_brand_changelist')}"
                )
                expect(page.get_by_text("Import finished: 2 created")).to_be_visible()

            page.goto(export_url, wait_until="networkidle")
            expect(
                page.get_by_role("button", name="Download", exact=True)
            ).to_be_visible()
            dims = overflow(page)
            assert dims["document"] <= dims["viewport"] + 1, (width, "export")
            context.close()

    assert not failures, failures
    assert set(Brand.objects.values_list("slug", flat=True)) == {"acme", "beta"}
