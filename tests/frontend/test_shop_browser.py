from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.shop import services
from apps.shop.models import Order, PackageTier, WindowKind
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


def catalog() -> PackageTier:
    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        duration_days=0,
        window=WindowKind.DAY,
        limit_files=10,
        limit_size=3,
        device_limit=3,
    )
    return PackageTier.objects.create(
        name="Premium",
        slug="premium",
        price=Decimal("9.00"),
        duration_days=30,
        window=WindowKind.PACKAGE,
        limit_files=30,
        limit_size=20,
        device_limit=5,
        applies_to_featured=True,
        ad_free=True,
        is_popular=True,
        description="More files, more devices and featured downloads.",
    )


@pytest.mark.django_db(transaction=True)
def test_public_shop_pages_have_no_console_csp_or_overflow_problems(live_server):
    catalog()
    paths = [
        reverse("shop:pricing"),
        reverse("shop:package_detail", kwargs={"slug": "premium"}),
    ]
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_signed_in_shop_pages_have_no_console_csp_or_overflow_problems(live_server):
    package = catalog()
    user = make_user("browser_shopper")
    cookie = login_cookie(user)
    order = services.create_order(user, package)
    paths = [
        reverse("shop:pricing"),
        reverse("shop:my_package"),
        reverse("shop:order_history"),
        reverse("shop:order_detail", kwargs={"pk": order.pk}),
        reverse("users:dashboard"),
    ]
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_pricing_page_summarises_and_compares_the_packages(live_server):
    catalog()
    with chromium() as browser:
        page = browser.new_page()
        page.goto(f"{live_server.url}{reverse('shop:pricing')}")
        ready(page)

        assert page.get_by_text(
            "30 files / 20 GB per 30 days · free + featured · 5 devices · $9/mo"
        ).first.is_visible()
        table = page.get_by_role("region", name="Package comparison")
        assert table.get_by_role("columnheader", name="Premium").is_visible()
        devices = table.get_by_role("row").filter(has_text="Devices")
        assert devices.get_by_role("cell").nth(1).inner_text().strip() == "5 devices"


@pytest.mark.django_db(transaction=True)
def test_requesting_a_package_shows_the_offline_payment_state_and_the_new_order(
    live_server,
):
    catalog()
    cookie = login_cookie(make_user("browser_requester"))
    failures: list[str] = []
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)
        page.goto(f"{live_server.url}{reverse('shop:pricing')}")
        ready(page)
        notice = page.get_by_role("status").filter(
            has_text="Online payments are not available"
        )
        assert notice.is_visible()
        assert page.get_by_text("Current plan").first.is_visible()

        page.get_by_role("link", name="Get this package").click()
        page.wait_for_url("**/packages/premium/")
        page.get_by_role("button", name="Request this package").click()
        page.wait_for_url("**/orders/*/")

        assert page.get_by_text("was received").is_visible()
        assert page.get_by_text(
            "Waiting for staff to confirm your payment"
        ).is_visible()
        assert page.get_by_text("Package · 30 days").is_visible()

    assert not failures, "\n".join(failures)
    order = Order.objects.get()
    assert (order.status, order.items.get().duration_days) == ("pending", 30)


@pytest.mark.django_db(transaction=True)
def test_my_package_page_shows_quota_meters_devices_and_perks(live_server):
    package = catalog()
    user = make_user("browser_meter_user")
    cookie = login_cookie(user)
    services.activate_package(user, package)
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        page.goto(f"{live_server.url}{reverse('shop:my_package')}")
        ready(page)

        assert page.locator("meter.eco-meter").count() >= 2
        assert page.get_by_text("Devices: 5 devices").is_visible()
        assert page.get_by_text("Ad-free downloads").first.is_visible()
