"""The firmware upload page in the browser: required cascade, file submit, layout."""

from __future__ import annotations

import pytest

from apps.firmwares.models import PendingFirmware
from tests.frontend.browser_support import (
    WIDTHS,
    dismiss_dialogs,
    login_cookie,
    make_user,
    session_context,
    visit_all,
    watch,
)
from tests.frontend.catalog_browser_support import (
    locked_chromium,
    open_page,
    option_titles,
    pick,
    seed_catalog,
)


@pytest.fixture(name="storage")
def firmware_storage_fixture(request, settings, tmp_path):
    request.getfixturevalue("transactional_db")
    settings.FIRMWARE_STORAGE_ROOT = tmp_path
    return tmp_path


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("storage")
def test_the_cascade_offers_only_the_chosen_parents_children(live_server):
    seed_catalog()
    cookies = login_cookie(make_user("cascade-user"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/upload/")

        assert page.locator("#upload-model").is_disabled()
        assert page.locator("#upload-variant").is_disabled()
        pick(page, "upload-brand", "acme", "Acme Mobile")
        assert page.locator("#upload-model").is_enabled()
        assert page.locator("#upload-variant").is_disabled()

        page.locator("#upload-model").click()
        page.keyboard.type("zenith")
        page.wait_for_timeout(600)
        assert not any(
            "Zenith" in title for title in option_titles(page, "upload-model")
        )
        page.locator("#upload-model").fill("")
        pick(page, "upload-model", "acme", "Acme One")
        assert page.locator("#upload-variant").is_enabled()

        page.locator("#upload-brand").fill("")
        page.locator("#upload-brand").blur()
        assert page.locator("#upload-model").is_disabled()
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("storage")
def test_a_missing_variant_leads_to_its_request_form(live_server):
    seed_catalog()
    cookies = login_cookie(make_user("missing-variant"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/upload/")

        link = page.get_by_role("link", name="Request this variant")
        assert link.is_visible()
        link.click()
        page.wait_for_url("**/firmwares/requests/variant/**")
        context.close()

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("storage")
def test_a_file_upload_becomes_a_pending_free_submission(live_server):
    data = seed_catalog()
    user = make_user("upload-user")
    cookies = login_cookie(user)
    failures: list[str] = []
    dialogs: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        open_page(page, live_server, "/firmwares/upload/")

        assert page.locator("select[name='access_tier']").count() == 0
        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_load_state("load")
        assert page.locator(".invalid-feedback").count() >= 1

        pick(page, "upload-brand", "acme", "Acme Mobile")
        pick(page, "upload-model", "acme", "Acme One")
        pick(page, "upload-variant", "glob", "Global")
        page.set_input_files(
            "input[name='file']",
            {
                "name": "acme-one.zip",
                "mimeType": "application/zip",
                "buffer": b"firmware-bytes",
            },
        )
        page.fill("#id_build_number", "BUILD.7")
        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_url("**/firmwares/upload/mine/")
        assert page.get_by_text("acme-one.zip").count() >= 1
        context.close()

    pending = PendingFirmware.objects.get()
    assert pending.uploader == user
    assert pending.uploaded_variant == data["variant"]
    assert (pending.admin_decision, pending.access_tier) == ("pending", "free")
    assert not dialogs
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("storage")
def test_upload_pages_fit_every_viewport(live_server):
    seed_catalog()
    cookies = login_cookie(make_user("upload-layout"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        visit_all(
            page,
            live_server,
            ["/firmwares/upload/", "/firmwares/upload/mine/"],
            failures,
            widths=WIDTHS,
        )
        context.close()

    assert not failures, "\n".join(failures)
