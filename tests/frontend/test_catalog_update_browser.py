"""Verified members can propose catalog corrections in the public interface."""

import pytest

from apps.firmwares.models import CatalogUpdateProposal
from tests.frontend.browser_support import (
    WIDTHS,
    login_cookie,
    make_user,
    overflow,
    session_context,
    watch,
)
from tests.frontend.catalog_browser_support import (
    locked_chromium,
    open_page,
    seed_catalog,
)


@pytest.mark.django_db(transaction=True)
def test_brand_correction_submission_and_status_are_responsive(live_server):
    catalog = seed_catalog()
    user = make_user("catalog-browser-author")
    cookies = login_cookie(user)
    failures: list[str] = []

    with locked_chromium() as browser:
        context = session_context(browser, live_server, cookies)
        page = context.new_page()
        watch(page, failures)
        open_page(page, live_server, "/firmwares/acme-mobile/")

        page.get_by_role("link", name="Suggest a correction").click()
        page.wait_for_url("**/firmwares/catalog-updates/brand/**")
        page.locator("#id_description").fill("Updated manufacturer information.")
        page.get_by_role("button", name="Submit for review").click()
        page.wait_for_url("**/firmwares/catalog-updates/mine/")
        assert page.get_by_text("Updated manufacturer information.").is_visible()
        assert page.get_by_text("Pending review").is_visible()

        for width in WIDTHS:
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(100)
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"], (
                f"Horizontal overflow at {width}px: {dimensions}"
            )
        context.close()

    proposal = CatalogUpdateProposal.objects.get()
    assert proposal.brand_id == catalog["acme"].pk
    assert proposal.submitted_by == user
    assert not failures, "\n".join(failures)
