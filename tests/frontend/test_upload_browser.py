"""The firmware upload page in the browser: required cascade, file submit, layout."""

from __future__ import annotations

import pytest

from apps.firmwares.models import PendingFirmware
from apps.marketplace.models import SellerProfile
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
        page.wait_for_selector("#upload-variant[role=combobox]")
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

        pick(page, "upload-brand", "acme", "Acme Mobile")
        pick(page, "upload-model", "acme", "Acme One")
        page.wait_for_selector("#upload-variant[role=combobox]")
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

        access = page.locator("select[name='access_tier']")
        assert access.input_value() == "free"
        assert access.locator("option[value='paid']").is_disabled()
        assert access.locator("option[value='featured']").is_disabled()
        note = page.locator("[data-upload-access-note]")
        assert (
            "Only approved sellers can offer paid or featured files"
            in note.inner_text()
        )
        assert (
            note.get_by_role("link", name="Become a seller").get_attribute("href")
            == "/marketplace/sell/"
        )
        assert page.locator("input[name='proposed_price']").count() == 0
        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_load_state("load")
        assert page.locator(".invalid-feedback").count() >= 1

        pick(page, "upload-brand", "acme", "Acme Mobile")
        pick(page, "upload-model", "acme", "Acme One")
        page.wait_for_selector("#upload-variant[role=combobox]")
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
def test_sellers_pick_a_tier_and_the_amount_appears_only_for_paid(live_server):
    seed_catalog()
    user = make_user("tier-seller")
    SellerProfile.objects.create(user=user, display_name="Tier Seller", verified=True)
    cookies = login_cookie(user)
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/upload/")

        tier = page.locator("select[name='access_tier']")
        price = page.locator("input[name='proposed_price']")
        assert tier.is_enabled() and tier.input_value() == "free"
        assert page.locator("[data-upload-access-note]").count() == 0
        assert price.is_hidden()

        tier.select_option("featured")
        assert price.is_hidden()
        tier.select_option("paid")
        price.wait_for(state="visible")
        assert price.get_attribute("aria-required") == "true"

        # Paid needs an amount above zero, explained inline before anything is sent.
        page.get_by_role("button", name="Submit for review").click()
        hint = page.locator("#id_proposed_price_errors")
        hint.wait_for(state="visible")
        assert "Enter the amount for a paid file." in hint.inner_text()
        price.fill("0")
        price.blur()
        page.wait_for_function(
            "() => document.getElementById('id_proposed_price_errors').textContent.includes('above zero')"
        )
        price.fill("4.50")
        hint.wait_for(state="hidden")

        tier.select_option("free")
        price.wait_for(state="hidden")
        context.close()

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
