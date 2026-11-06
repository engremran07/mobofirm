import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.blog.models import Post, PostStatus
from apps.firmwares.models import Brand, Model
from tests.frontend.browser_support import collect_console


@pytest.mark.django_db(transaction=True)
def test_model_blog_displays_empty_firmware_inventory_on_mobile_and_desktop(
    live_server,
):
    author = get_user_model().objects.create_user(
        username="browser-empty-inventory-author",
        email="browser-empty-inventory-author@example.com",
        password="SafePassword123!",
    )
    brand = Brand.objects.create(
        name="Browser Empty Inventory Brand",
        slug="browser-empty-inventory-brand",
    )
    model = Model.objects.create(
        brand=brand,
        name="Browser Empty Inventory Phone",
        slug="browser-empty-inventory-phone",
    )
    post = Post.objects.create(
        title="Browser Empty Inventory Phone firmware",
        slug="browser-empty-inventory-phone-firmware",
        body="<p>Firmware inventory for this model.</p>",
        author=author,
        firmware_brand=brand,
        firmware_model=model,
    )
    Post.objects.filter(pk=post.pk).update(
        status=PostStatus.PUBLISHED,
        is_published=True,
        publish_at=timezone.now(),
        published_at=timezone.now(),
    )
    errors: list[str] = []
    warnings: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for firmware-blog browser checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.on("pageerror", lambda error: errors.append(str(error)))
        collect_console(page, errors, warnings)
        page.goto(
            f"{live_server.url}"
            f"{reverse('blog:post_detail', kwargs={'slug': post.slug})}",
            wait_until="load",
        )

        assert page.get_by_role("heading", name="Firmware downloads").is_visible()
        assert page.get_by_text(
            "No public firmware records are available.", exact=True
        ).is_visible()
        for width, height in ((390, 844), (1365, 900)):
            page.set_viewport_size({"width": width, "height": height})
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth
                })"""
            )
            overflowing_elements = page.evaluate(
                """() => Array.from(document.querySelectorAll("body *"))
                    .map(element => {
                        const rect = element.getBoundingClientRect();
                        return {
                            tag: element.tagName.toLowerCase(),
                            id: element.id,
                            className: typeof element.className === "string"
                                ? element.className
                                : "",
                            right: Math.round(rect.right),
                            width: Math.round(rect.width)
                        };
                    })
                    .filter(element =>
                        element.right > document.documentElement.clientWidth + 1
                    )
                    .slice(0, 10)"""
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1, (
                f"Overflow at {width}px: {dimensions}; elements={overflowing_elements}"
            )

        assert not errors, f"Browser errors: {errors}"
        browser.close()

    assert not warnings, f"Browser warnings: {warnings}"


@pytest.mark.django_db(transaction=True)
def test_editorial_workspace_shows_automated_and_manual_posts(live_server):
    staff = get_user_model().objects.create_user(
        username="browser-editorial-manager",
        email="browser-editorial-manager@example.test",
        password="Browser-editorial-test-password-123!",
        is_staff=True,
    )
    manual = Post.objects.create(
        title="Browser manual editorial draft",
        body="<p>A manual draft visible to the editorial team.</p>",
        author=staff,
    )
    automated = Post.objects.create(
        title="Browser automated review post",
        body="<p>Automatically created content awaiting review.</p>",
        author=staff,
        status=PostStatus.IN_REVIEW,
        is_ai_generated=True,
    )
    client = Client()
    client.force_login(staff)
    session_cookie = client.cookies[settings.SESSION_COOKIE_NAME]
    errors: list[str] = []
    warnings: list[str] = []

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as error:
            pytest.fail(
                "Chromium is required for editorial workspace checks. "
                "Install it with `.venv\\Scripts\\python.exe -m playwright install chromium`.\n"
                f"{error}"
            )

        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie.value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        collect_console(page, errors, warnings)
        page.goto(
            f"{live_server.url}{reverse('blog:manage_posts')}",
            wait_until="load",
        )
        assert page.get_by_role("heading", name="Manage journal posts").is_visible()
        assert page.get_by_role("row", name=manual.title).is_visible()
        assert page.get_by_role("row", name=automated.title).is_visible()
        assert (
            page.get_by_role("row", name=automated.title)
            .get_by_text("Automated", exact=True)
            .is_visible()
        )
        for width, height in ((390, 844), (1365, 900)):
            page.set_viewport_size({"width": width, "height": height})
            dimensions = page.evaluate(
                """() => ({
                    viewport: document.documentElement.clientWidth,
                    document: document.documentElement.scrollWidth
                })"""
            )
            overflowing_elements = page.evaluate(
                """() => Array.from(document.querySelectorAll("body *"))
                    .map(element => {
                        const rect = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        return {
                            tag: element.tagName.toLowerCase(),
                            id: element.id,
                            className: typeof element.className === "string"
                                ? element.className
                                : "",
                            right: Math.round(rect.right),
                            width: Math.round(rect.width),
                            overflowX: style.overflowX
                        };
                    })
                    .filter(element =>
                        element.right > document.documentElement.clientWidth + 1
                    )
                    .slice(0, 10)"""
            )
            table_layout = page.evaluate(
                """() => {
                    const table = document.querySelector(".catalog-table");
                    return [table, table?.parentElement, table?.parentElement?.parentElement]
                        .filter(Boolean)
                        .map(element => {
                            const style = getComputedStyle(element);
                            const rect = element.getBoundingClientRect();
                            return {
                                tag: element.tagName.toLowerCase(),
                                className: element.className,
                                left: Math.round(rect.left),
                                right: Math.round(rect.right),
                                width: Math.round(rect.width),
                                overflowX: style.overflowX,
                                maxWidth: style.maxWidth,
                                minWidth: style.minWidth
                            };
                        });
                }"""
            )
            assert dimensions["document"] <= dimensions["viewport"] + 1, (
                f"Overflow at {width}px: {dimensions}; "
                f"elements={overflowing_elements}; table_layout={table_layout}"
            )

        assert not errors, f"Browser errors: {errors}"
        browser.close()

    assert not warnings, f"Browser warnings: {warnings}"
