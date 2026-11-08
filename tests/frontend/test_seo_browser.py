from __future__ import annotations

import json
import re

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from apps.seo import scan
from apps.seo.crawler import parse_html
from apps.seo.models import (
    ChangeLedger,
    Issue,
    LinkSuggestion,
    Redirect,
    SEOJob,
)
from tests.backend.seo.seo_helpers import SITE, make_catalog, make_post
from tests.frontend.browser_support import (
    chromium,
    login_cookie,
    ready,
    session_context,
    watch,
)

NOISE = ("Failed to load resource:",)


def collect_errors(page, errors):
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    page.on(
        "console",
        lambda message: (
            errors.append(f"console: {message.text}")
            if message.type == "error" and not message.text.startswith(NOISE)
            else None
        ),
    )


def launch(playwright):
    try:
        return playwright.chromium.launch()
    except PlaywrightError as error:
        pytest.fail(
            "Chromium is required for SEO browser checks. Install it with "
            f"`python -m playwright install chromium`.\n{error}"
        )


def overflow(page):
    return page.evaluate(
        """() => ({
            viewport: document.documentElement.clientWidth,
            document: document.documentElement.scrollWidth
        })"""
    )


@pytest.mark.django_db(transaction=True)
def test_sitemaps_and_robots_render_without_csp_or_console_errors(live_server):
    make_catalog()
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page()
        collect_errors(page, errors)
        for path in ("/sitemap.xml", "/sitemap-brands.xml", "/robots.txt"):
            response = page.goto(f"{live_server.url}{path}", wait_until="load")
            assert response is not None and response.status == 200, path
        page.goto(f"{live_server.url}/sitemap.xml", wait_until="load")
        assert "sitemap-brands.xml" in page.content()
        browser.close()

    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_view_source_head_is_correct_on_key_public_pages(live_server, settings):
    settings.SITE_URL = SITE
    data = make_catalog()
    paths = [
        "/",
        reverse("firmwares:brand_list"),
        reverse("firmwares:brand_detail", args=[data.brand.slug]),
        reverse("firmwares:model_detail", args=[data.brand.slug, data.model.slug]),
        reverse("firmwares:chipset_detail", args=[data.chipset.slug]),
        reverse("firmwares:firmware_detail", args=["official", data.firmware.pk]),
        reverse("firmwares:flashing_tools"),
        reverse("firmwares:flashing_tool_detail", args=[data.tool.slug]),
        reverse("blog:post_list"),
        data.post.get_absolute_url(),
        reverse("tags:detail", args=[data.tag.slug]),
        reverse("pages:page", args=[data.page.slug]),
    ]
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 1365, "height": 900})
        collect_errors(page, errors)
        for path in paths:
            url = f"{live_server.url}{path}"
            # The raw response is exactly what a crawler sees ("view source").
            raw = page.request.get(url)
            assert raw.status == 200, path
            head = parse_html(raw.text())
            assert len(head.titles) == 1 and head.titles[0], path
            assert len(head.descriptions) == 1 and head.descriptions[0], path
            assert len(head.robots_meta) == 1, path
            assert head.canonicals == [f"{SITE}{'/' if path == '/' else path}"], path
            assert "x-robots-tag" not in raw.headers, path
            assert head.jsonld_errors == 0
            assert "BreadcrumbList" in head.jsonld_types or path == "/"

            page.goto(url, wait_until="load")
            counts = page.evaluate(
                """() => ({
                    titles: document.querySelectorAll("head title").length,
                    canonicals: document.querySelectorAll('head link[rel="canonical"]').length,
                    robots: document.querySelectorAll('head meta[name="robots"]').length,
                    descriptions: document.querySelectorAll('head meta[name="description"]').length,
                    ogTitle: document.querySelectorAll('head meta[property="og:title"]').length,
                    title: document.title,
                })"""
            )
            assert counts["titles"] == counts["canonicals"] == counts["robots"] == 1
            assert counts["descriptions"] == counts["ogTitle"] == 1
            assert counts["title"] == head.titles[0]
            blocks = page.evaluate(
                """() => Array.from(
                    document.querySelectorAll('script[type="application/ld+json"]')
                ).map(node => node.textContent)"""
            )
            for block in blocks:
                json.loads(block)
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, path
        browser.close()

    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_noindex_surfaces_in_the_served_head_for_search_and_variants(
    live_server, settings
):
    settings.SITE_URL = SITE
    make_catalog()

    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page()
        for path in (
            f"{reverse('firmwares:universal_search')}?q=atlas",
            f"{reverse('firmwares:browse')}?q=atlas",
        ):
            head = parse_html(page.request.get(f"{live_server.url}{path}").text())
            assert head.robots_meta[0].startswith("noindex"), path
            assert head.jsonld_types == []
        admin = page.request.get(f"{live_server.url}/admin/login/")
        assert admin.headers["x-robots-tag"] == "noindex, nofollow"
        browser.close()


@pytest.mark.django_db(transaction=True)
def test_seo_control_center_is_usable_responsive_and_error_free(live_server):
    make_catalog()
    admin_user = get_user_model().objects.create_superuser(
        username="seo-browser-admin",
        email="seo-browser-admin@example.com",
        password="browser-pass-12345",
    )
    Issue.objects.create(
        code="missing_title",
        severity="error",
        url=f"{SITE}/a/",
        message="The page has no <title>.",
        fingerprint="browser-1",
    )
    Issue.objects.create(
        code="slow_response",
        severity="info",
        url=f"{SITE}/b/",
        message="Response took 4000 ms.",
        fingerprint="browser-2",
    )
    proposal = ChangeLedger.objects.create(
        idempotency_key="browser-proposal",
        kind="redirect_create",
        action="create",
        target_model="seo.redirect",
        target_label="/old-browser/ -> /new-browser/",
        after={
            "source": "/old-browser/",
            "target": "/new-browser/",
            "is_permanent": True,
            "is_active": True,
        },
        reason="Browser test proposal",
        source="test",
        requires_approval=True,
    )
    client = Client()
    client.force_login(admin_user)
    session = client.session
    session.pop("device_registration_prompt", None)
    session.save()
    cookie = client.cookies[django_settings.SESSION_COOKIE_NAME]
    errors: list[str] = []

    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(viewport={"width": 1365, "height": 900})
        context.add_cookies(
            [
                {
                    "name": django_settings.SESSION_COOKIE_NAME,
                    "value": cookie.value,
                    "url": live_server.url,
                }
            ]
        )
        page = context.new_page()
        collect_errors(page, errors)

        for name in (
            "dashboard",
            "inventory",
            "inspect",
            "issues",
            "links",
            "guides",
            "ledger",
            "jobs",
            "settings",
        ):
            for width in (1365, 768, 360):
                page.set_viewport_size({"width": width, "height": 800})
                response = page.goto(
                    f"{live_server.url}{reverse(f'seo:{name}')}", wait_until="load"
                )
                assert response is not None and response.status == 200, name
                assert page.get_by_role("navigation", name="SEO sections").is_visible()
                assert page.locator("h1.mf-ui-title").is_visible(), name
                assert page.locator("#unfold-sidebar, nav[aria-label], aside").count()
                dimensions = overflow(page)
                assert dimensions["document"] <= dimensions["viewport"] + 1, (
                    f"{name} overflows at {width}px: {dimensions}"
                )

        page.set_viewport_size({"width": 1365, "height": 900})
        page.goto(f"{live_server.url}{reverse('seo:dashboard')}", wait_until="load")
        assert page.get_by_text("No completed crawl yet").is_visible()
        assert page.get_by_role("button", name="Run crawl and audit now").is_visible()

        page.goto(f"{live_server.url}{reverse('seo:issues')}", wait_until="load")
        page.get_by_label("Severity").select_option("error")
        page.get_by_role("button", name="Filter").click()
        page.wait_for_url(re.compile(r"severity=error"))
        assert page.get_by_text("The page has no <title>.").is_visible()
        assert page.get_by_text("Response took 4000 ms.").count() == 0

        page.goto(f"{live_server.url}{reverse('seo:inspect')}", wait_until="load")
        page.get_by_label("Page path or URL").fill("/firmwares/nova/atlas-one/")
        page.keyboard.press("Enter")
        page.wait_for_url(re.compile(r"url="))
        assert page.get_by_text("What the resolver intends").is_visible()
        assert page.get_by_text("Atlas One firmware download").first.is_visible()

        page.goto(
            f"{live_server.url}{reverse('seo:ledger')}?status=proposed",
            wait_until="load",
        )
        page.get_by_role("button", name="Approve and apply").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("is now Applied").is_visible()
        browser.close()

    proposal.refresh_from_db()
    assert proposal.status == ChangeLedger.Status.APPLIED
    assert Redirect.objects.filter(source="/old-browser/").exists()
    assert errors == []


@pytest.mark.django_db(transaction=True)
def test_issues_board_review_and_inline_fix_flow(live_server, settings):
    settings.SITE_URL = SITE
    data = make_catalog()
    long_title = "A very long post title that keeps going well past sixty characters"
    post = make_post(
        "long-title-post",
        "Short",
        data.post.author,
        data.category,
        seo_title=long_title,
        body="<p>" + " ".join(["word"] * 200) + "</p>",
        summary="s" * 90,
    )
    admin_user = get_user_model().objects.create_superuser(
        username="seo-review-admin",
        email="seo-review-admin@example.com",
        password="Pass-12345!",
    )
    scan.run_scan()
    issue = Issue.objects.get(
        code="title_too_long", url=post.get_absolute_url(), status=Issue.Status.OPEN
    )
    cookie = login_cookie(admin_user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)

        page.goto(f"{live_server.url}{reverse('seo:dashboard')}", wait_until="load")
        assert page.get_by_text("Last content scan").is_visible()
        assert page.get_by_role("button", name="Run scan now").first.is_visible()

        for width in (360, 768, 1365):
            page.set_viewport_size({"width": width, "height": 900})
            page.goto(
                f"{live_server.url}{reverse('seo:issues')}?group=posts",
                wait_until="load",
            )
            ready(page)
            card = page.locator(f"#issue-{issue.pk}")
            assert card.is_visible(), width
            assert card.get_by_text("Title is too long").first.is_visible()
            link = card.get_by_role("link", name=re.compile("Review and fix"))
            link.click()
            panel = page.locator(f"#fix-{issue.pk} textarea")
            panel.wait_for(state="visible")
            assert panel.input_value().startswith("A very long post title")
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, (
                f"board with panel overflows at {width}px: {dimensions}"
            )
            page.evaluate("document.documentElement.classList.add('dark')")
            dimensions = overflow(page)
            assert dimensions["document"] <= dimensions["viewport"] + 1, (
                f"dark board overflows at {width}px: {dimensions}"
            )
            page.evaluate("document.documentElement.classList.remove('dark')")

        page.set_viewport_size({"width": 1365, "height": 900})
        page.goto(
            f"{live_server.url}{reverse('seo:issues')}?group=posts", wait_until="load"
        )
        ready(page)
        card = page.locator(f"#issue-{issue.pk}")
        card.get_by_role("link", name=re.compile("Review and fix")).click()
        field = page.locator(f"#fix-{issue.pk} textarea")
        field.wait_for(state="visible")
        field.fill("Flash the Atlas One safely, step by step")
        page.locator(f"#fix-{issue.pk}").get_by_role("button", name="Apply fix").click()
        page.wait_for_url(re.compile(r"/seo/issues/"))
        assert page.get_by_text("Fixed: Title is too long").is_visible()
        assert page.locator(f"#issue-{issue.pk}").count() == 0

        page.goto(f"{live_server.url}{post.get_absolute_url()}", wait_until="load")
        assert page.title() == "Flash the Atlas One safely, step by step"
        page.goto(f"{live_server.url}{reverse('seo:ledger')}", wait_until="load")
        assert page.get_by_role("button", name="Roll back").first.is_visible()
        page.get_by_role("button", name="Roll back").first.click()
        page.wait_for_load_state("load")
        assert page.get_by_text("is now Rolled back").is_visible()
        page.goto(
            f"{live_server.url}{reverse('seo:issues')}?group=posts", wait_until="load"
        )
        assert page.locator(f"#issue-{issue.pk}").is_visible()

    issue.refresh_from_db()
    assert issue.status == Issue.Status.OPEN
    assert not failures, "\n".join(failures)


@pytest.mark.django_db(transaction=True)
def test_scan_queue_and_interlinking_actions_run_from_the_screens(
    live_server, settings
):
    settings.SITE_URL = SITE
    make_catalog()
    SEOJob.objects.create(
        kind="content_changed",
        job_key="browser-job",
        payload={
            "label": "blog.post",
            "pk": "1",
            "url": "/blog/x/",
            "public": False,
            "previous_url": "",
            "previous_public": False,
        },
    )
    admin_user = get_user_model().objects.create_superuser(
        username="seo-actions-admin",
        email="seo-actions-admin@example.com",
        password="Pass-12345!",
    )
    cookie = login_cookie(admin_user)
    failures: list[str] = []

    with chromium() as browser:
        context = session_context(browser, live_server, cookie)
        page = context.new_page()
        watch(page, failures)

        page.goto(f"{live_server.url}{reverse('seo:dashboard')}", wait_until="load")
        page.get_by_role("button", name="Run scan now").first.click()
        page.wait_for_load_state("load")
        assert page.get_by_text(
            re.compile(r"URLs checked, \d+ open issues")
        ).is_visible()
        assert page.get_by_text("Pages in inventory").is_visible()

        page.goto(f"{live_server.url}{reverse('seo:jobs')}", wait_until="load")
        page.get_by_role("button", name="Process pending jobs now").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("The queue is empty").is_visible()

        page.goto(f"{live_server.url}{reverse('seo:links')}", wait_until="load")
        page.get_by_role("button", name="Generate proposals").click()
        page.wait_for_load_state("load")
        assert page.get_by_text("new proposals").is_visible()
        page.get_by_role("button", name=re.compile("^Accept")).first.click()
        page.wait_for_load_state("load")
        assert page.get_by_text(re.compile("Link accepted")).is_visible()

        page.goto(f"{live_server.url}{reverse('seo:guides')}", wait_until="load")
        assert page.get_by_text("Covered").first.is_visible()
        page.get_by_role("button", name=re.compile("^Refresh")).first.click()
        page.wait_for_load_state("load")
        assert page.get_by_text(re.compile("Refreshed 1 guide post")).is_visible()
        context.close()

    assert SEOJob.objects.filter(status=SEOJob.Status.PENDING).count() == 0
    assert LinkSuggestion.objects.filter(status="accepted").count() == 1
    assert not failures, "\n".join(failures)
