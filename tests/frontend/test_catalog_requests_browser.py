"""End-user catalogue requests in the browser: cascade, dedupe hints, CTAs, layout."""

from __future__ import annotations

import pytest

from apps.firmwares.models import (
    BrandCreationRequest,
    ModelCreationRequest,
    VariantCreationRequest,
)
from tests.frontend.browser_support import (
    WIDTHS,
    dismiss_dialogs,
    login_cookie,
    make_user,
    overflow,
    ready,
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

REQUEST_PATHS = [
    "/firmwares/requests/",
    "/firmwares/requests/brand/",
    "/firmwares/requests/model/",
    "/firmwares/requests/variant/",
    "/firmwares/requests/firmware/",
    "/firmwares/requests/chipset/",
    "/firmwares/requests/mine/",
]


@pytest.mark.django_db(transaction=True)
def test_signed_out_visitors_get_a_sign_in_then_request_prompt(live_server):
    seed_catalog()
    failures: list[str] = []

    with locked_chromium() as browser:
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        watch(page, failures)
        open_page(page, live_server, "/firmwares/brands/")

        cta = page.locator("[data-request-cta='brand']").first
        assert cta.is_visible()
        href = cta.get_attribute("href") or ""
        assert "login" in href
        assert "next=" in href

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_a_model_request_needs_the_brand_and_takes_an_optional_chipset(live_server):
    data = seed_catalog()
    user = make_user("model-requester")
    cookies = login_cookie(user)
    failures: list[str] = []
    dialogs: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        dismiss_dialogs(page, dialogs)
        open_page(page, live_server, "/firmwares/requests/model/")

        page.fill("#id_name", "Acme Three")
        page.get_by_role("button", name="Send model request").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("Choose the brand from the list.").count() >= 1

        pick(page, "request-brand", "acme", "Acme Mobile")
        pick(page, "request-chipset", "helio", "Helio G99")
        page.fill("#id_name", "Acme Three")
        page.wait_for_selector("#request-check >> text=No existing match")
        page.get_by_role("button", name="Send model request").click()
        page.wait_for_url("**/firmwares/requests/mine/")
        assert page.get_by_text("Acme Three").count() >= 1
        assert page.get_by_text("pending", exact=False).count() >= 1
        context.close()

    pending = ModelCreationRequest.objects.get()
    assert (pending.brand, pending.chipset, pending.requested_by) == (
        data["acme"],
        data["chipset"],
        user,
    )
    assert not dialogs
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_existing_names_are_flagged_before_sending(live_server):
    seed_catalog()
    cookies = login_cookie(make_user("dupe-requester"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/requests/brand/")

        page.fill("#id_name", "Acme Mobile")
        page.wait_for_selector("#request-check >> text=already exists")
        page.get_by_role("button", name="Send brand request").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("already exists in the catalogue").count() >= 1
        context.close()

    assert not BrandCreationRequest.objects.exists()
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_the_variant_form_cascades_brand_then_model(live_server):
    data = seed_catalog()
    cookies = login_cookie(make_user("variant-requester"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/requests/variant/")

        assert page.locator("#request-model").is_disabled()
        pick(page, "request-brand", "acme", "Acme Mobile")
        assert page.locator("#request-model").is_enabled()

        page.locator("#request-model").click()
        page.keyboard.type("zenith")
        page.wait_for_timeout(600)
        assert not any(
            "Zenith" in title for title in option_titles(page, "request-model")
        )
        page.locator("#request-model").fill("")
        pick(page, "request-model", "acme", "Acme One")

        page.fill("#id_name", "India")
        page.fill("#id_region", "IN")
        page.get_by_role("button", name="Send variant request").click()
        page.wait_for_url("**/firmwares/requests/mine/")
        context.close()

    pending = VariantCreationRequest.objects.get()
    assert pending.model == data["model"]
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_request_pages_fit_every_viewport_and_stay_quiet(live_server):
    seed_catalog()
    cookies = login_cookie(make_user("layout-user"))
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, REQUEST_PATHS, failures, widths=WIDTHS)

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            open_page(page, live_server, "/firmwares/requests/variant/")
            ready(page)
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1
        context.close()

    assert not failures, "\n".join(failures)
