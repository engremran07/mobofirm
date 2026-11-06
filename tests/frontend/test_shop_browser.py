from __future__ import annotations

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.shop import services
from apps.shop.models import Order, PackageTier, Product
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    make_user,
    ready,
    session_context,
    visit_all,
    watch,
)


def catalog() -> Product:
    PackageTier.objects.create(
        name="Free",
        slug="free",
        is_default=True,
        daily_download_limit=3,
        monthly_download_limit=30,
    )
    pro = PackageTier.objects.create(
        name="Pro",
        slug="pro",
        tier_level="pro",
        price_monthly=Decimal("9.00"),
        price_yearly=Decimal("90.00"),
        daily_download_limit=-1,
        monthly_download_limit=-1,
        access_engineering=True,
        ad_free=True,
        is_featured=True,
    )
    return Product.objects.create(
        name="Pro package",
        slug="pro-package",
        product_type="package",
        price=Decimal("9.00"),
        package_tier=pro,
        description="Unlimited downloads for power users.",
    )


@pytest.mark.django_db(transaction=True)
def test_public_shop_pages_have_no_console_csp_or_overflow_problems(live_server):
    catalog()
    paths = [
        reverse("shop:pricing"),
        reverse("shop:product_list"),
        reverse("shop:product_detail", kwargs={"slug": "pro-package"}),
    ]
    failures: list[str] = []
    with chromium() as browser:
        page = browser.new_page()
        watch(page, failures)
        visit_all(page, live_server, paths, failures)

    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_signed_in_shop_pages_have_no_console_csp_or_overflow_problems(live_server):
    product = catalog()
    user = make_user("browser_shopper")
    cookie = login_cookie(user)
    order = services.create_order(user, product, "monthly")
    paths = [
        reverse("shop:pricing"),
        reverse("shop:my_subscriptions"),
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
        assert page.get_by_text("Your package").count() == 0

        page.get_by_role("link", name="Request this package").click()
        page.wait_for_url("**/products/pro-package/")
        page.get_by_label("Billing period").select_option("yearly")
        page.get_by_role("button", name="Request this order").click()
        page.wait_for_url("**/orders/")

        assert page.get_by_text("was received").is_visible()
        row = page.get_by_role("row").filter(has_text="Pro package")
        assert row.get_by_text("Pending payment").is_visible()
        row.get_by_role("link", name="#").first.click()
        page.wait_for_url("**/orders/*/")
        assert page.get_by_text(
            "Waiting for staff to confirm your payment"
        ).is_visible()

    assert not failures, "\n".join(failures)
    order = Order.objects.get()
    assert (order.status, order.items.get().billing_cycle) == ("pending", "yearly")


@pytest.mark.django_db(transaction=True)
def test_package_page_shows_quota_meters_and_cards_do_not_overlap(live_server):
    catalog()
    cookie = login_cookie(make_user("browser_meter_user"))
    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        page.goto(f"{live_server.url}{reverse('shop:my_subscriptions')}")
        ready(page)
        meters = page.locator("meter.eco-meter")
        assert meters.count() == 4
        assert page.get_by_text("not applied to downloads").is_visible()

        page.goto(f"{live_server.url}{reverse('shop:pricing')}")
        page.set_viewport_size({"width": 1365, "height": 900})
        cards = page.locator("article.eco-tier").all()
        boxes = [card.bounding_box() for card in cards]
        assert len(boxes) == 2
        first, second = boxes
        assert first is not None and second is not None
        separate = (
            first["x"] + first["width"] <= second["x"] + 1
            or second["x"] + second["width"] <= first["x"] + 1
            or first["y"] + first["height"] <= second["y"] + 1
        )
        assert separate


@pytest.mark.django_db(transaction=True)
def test_anonymous_visitors_are_sent_to_sign_in_for_orders(live_server):
    catalog()
    with chromium() as browser:
        page = browser.new_page()
        page.goto(f"{live_server.url}{reverse('shop:order_history')}")
        assert "/login" in page.url
